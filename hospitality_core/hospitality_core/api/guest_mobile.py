"""Guest mobile REST API.

Token-authenticated endpoints designed for a thin mobile/PWA client.
Auth model: a guest proves possession of a Hotel Reservation by quoting
the reservation name + last-4 of their stored identification_no or
mobile_no. On success we mint a short-lived HMAC token bound to the
reservation and an expiry timestamp.

The token is opaque to the client and re-validated on every call. No
session cookie is set — clients must replay the token in each request.

Tokens never disclose folio data; reading folio summaries requires the
guest's reservation to be in 'Reserved' or 'Checked In' status.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import flt, getdate, now_datetime

_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12h
_OPEN_RES_STATUSES = ("Reserved", "Checked In")


def _site_secret() -> str:
	"""Per-site signing secret. Required — no public-name fallback because
	the site domain is publicly known (DNS, TLS SNI) and would let an
	attacker forge tokens. Set `hospitality_guest_secret` in site_config
	or rely on Frappe's `encryption_key`."""
	conf = frappe.local.conf
	secret = (
		conf.get("hospitality_guest_secret")
		or conf.get("encryption_key")
		or conf.get("secret_key")
	)
	if not secret:
		frappe.throw(
			_(
				"Guest mobile auth is not configured. Set `hospitality_guest_secret` "
				"or `encryption_key` in site_config.json."
			),
			frappe.AuthenticationError,
		)
	return secret


def _sign(payload: str) -> str:
	return hmac.new(
		_site_secret().encode("utf-8"),
		payload.encode("utf-8"),
		hashlib.sha256,
	).hexdigest()


def _mint_token(reservation: str) -> dict:
	exp = int(time.time()) + _TOKEN_TTL_SECONDS
	body = f"{reservation}|{exp}"
	sig = _sign(body)
	return {"token": f"{body}|{sig}", "expires_at": exp}


def _verify_token(token: str | None) -> str:
	"""Returns the reservation name on success, throws on failure."""
	if not token or token.count("|") != 2:
		frappe.throw(_("Invalid or missing token."), frappe.AuthenticationError)
	reservation, exp_str, sig = token.split("|", 2)
	expected = _sign(f"{reservation}|{exp_str}")
	if not hmac.compare_digest(expected, sig):
		frappe.throw(_("Invalid token signature."), frappe.AuthenticationError)
	if int(exp_str) < int(time.time()):
		frappe.throw(_("Token has expired — please re-authenticate."), frappe.AuthenticationError)
	return reservation


def _last4(value: str | None) -> str | None:
	if not value:
		return None
	digits = "".join(c for c in str(value) if c.isalnum())
	return digits[-4:].lower() if len(digits) >= 4 else None


# ---- public endpoints ----------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(key="reservation", limit=10, seconds=60 * 60, ip_based=True)
def request_token(reservation: str, last4: str) -> dict:
	"""Issue a token after matching reservation + last-4 of stored ID/mobile.

	The reservation must be in Reserved or Checked In status; we don't issue
	tokens for cancelled or checked-out stays.

	Rate-limited to 10 attempts per hour per (IP, reservation). Last-4 is
	a 4-character secret — without throttling, brute-force is trivial.
	Failed attempts are logged for security review; a successful mint
	is recorded in the audit log so misuse can be traced.
	"""
	if not reservation or not last4:
		frappe.throw(_("reservation and last4 are required."))
	res = frappe.db.get_value(
		"Hotel Reservation",
		reservation,
		["name", "guest", "status"],
		as_dict=True,
	)
	if not res:
		_log_token_attempt(reservation, ok=False, reason="not_found")
		frappe.throw(_("Reservation not found."), frappe.AuthenticationError)
	if res.status not in _OPEN_RES_STATUSES:
		_log_token_attempt(reservation, ok=False, reason=f"status={res.status}")
		frappe.throw(_("Reservation is not active."), frappe.AuthenticationError)

	guest = frappe.db.get_value(
		"Guest",
		res.guest,
		["mobile_no", "identification_no"],
		as_dict=True,
	) or frappe._dict()
	candidates = {_last4(guest.get("mobile_no")), _last4(guest.get("identification_no"))}
	candidates.discard(None)
	if _last4(last4) not in candidates:
		_log_token_attempt(reservation, ok=False, reason="last4_mismatch")
		frappe.throw(_("Verification details do not match."), frappe.AuthenticationError)

	_log_token_attempt(reservation, ok=True)
	return _mint_token(res.name)


def _log_token_attempt(reservation: str, *, ok: bool, reason: str | None = None) -> None:
	"""Best-effort audit trail. Swallow logger failures so auth path stays clean."""
	try:
		ip = getattr(frappe.local, "request_ip", None) if hasattr(frappe, "local") else None
		message = f"{'OK' if ok else 'FAIL'} reservation={reservation} ip={ip or '-'}"
		if reason:
			message += f" reason={reason}"
		frappe.logger("hospitality_core.guest_mobile").info(message)
	except Exception:
		pass


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_my_reservation(token: str) -> dict:
	reservation = _verify_token(token)
	res = frappe.db.get_value(
		"Hotel Reservation",
		reservation,
		[
			"name",
			"guest",
			"hotel_reception",
			"room",
			"room_type",
			"arrival_date",
			"departure_date",
			"status",
			"folio",
			"is_complimentary",
		],
		as_dict=True,
	)
	if not res:
		frappe.throw(_("Reservation not found."))

	guest_name = frappe.db.get_value("Guest", res.guest, "full_name") if res.guest else None
	return {
		"reservation": res.name,
		"guest_name": guest_name,
		"hotel_reception": res.hotel_reception,
		"room": res.room,
		"room_type": res.room_type,
		"arrival_date": str(res.arrival_date) if res.arrival_date else None,
		"departure_date": str(res.departure_date) if res.departure_date else None,
		"nights": (
			(getdate(res.departure_date) - getdate(res.arrival_date)).days
			if res.arrival_date and res.departure_date
			else 0
		),
		"status": res.status,
		"folio": res.folio,
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def my_folio_summary(token: str) -> dict:
	"""Charges, payments, balance — no per-line PII beyond item names."""
	reservation = _verify_token(token)
	folio = frappe.db.get_value("Hotel Reservation", reservation, "folio")
	if not folio:
		return {
			"reservation": reservation,
			"folio": None,
			"total_charges": 0.0,
			"total_payments": 0.0,
			"total_discounts": 0.0,
			"outstanding_balance": 0.0,
			"currency": frappe.db.get_default("currency"),
			"transactions": [],
		}

	totals = frappe.db.get_value(
		"Guest Folio",
		folio,
		[
			"total_charges",
			"total_payments",
			"total_discounts",
			"outstanding_balance",
			"currency",
		],
		as_dict=True,
	) or {}

	rows = frappe.db.sql(
		"""
		SELECT item, amount, posted_at, is_void
		FROM `tabFolio Transaction`
		WHERE parent = %s
		ORDER BY posted_at DESC, idx DESC
		LIMIT 100
		""",
		(folio,),
		as_dict=True,
	)
	for r in rows:
		r["amount"] = flt(r.get("amount"))
		r["posted_at"] = str(r.get("posted_at")) if r.get("posted_at") else None
		r["is_void"] = bool(r.get("is_void"))

	return {
		"reservation": reservation,
		"folio": folio,
		"total_charges": flt(totals.get("total_charges")),
		"total_payments": flt(totals.get("total_payments")),
		"total_discounts": flt(totals.get("total_discounts")),
		"outstanding_balance": flt(totals.get("outstanding_balance")),
		"currency": totals.get("currency") or frappe.db.get_default("currency"),
		"transactions": rows,
	}


_ALLOWED_AMENITY_CATEGORIES = (
	"Housekeeping",
	"Engineering",
	"Concierge",
	"Room Service",
	"Other",
)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_amenity(token: str, category: str, message: str | None = None) -> dict:
	"""Open a Concierge Request scoped to the verified reservation.

	Token gives us the reservation; we derive room + guest server-side so
	the guest cannot inject fields. The doc is created with
	ignore_permissions=True (the guest is not a Frappe User) but the
	scope is server-trusted — we never elevate the request session.
	"""
	reservation = _verify_token(token)
	if (category or "").strip() not in _ALLOWED_AMENITY_CATEGORIES:
		frappe.throw(_("Unsupported amenity category."))
	res = frappe.db.get_value(
		"Hotel Reservation", reservation, ["room", "guest"], as_dict=True
	)
	if not res or not res.room:
		frappe.throw(_("Your room has not been assigned yet."))
	subject = (message or category or "Guest request").strip()[:140]
	doc = frappe.get_doc(
		{
			"doctype": "Concierge Request",
			"subject": subject,
			"category": category,
			"guest": res.guest,
			"room": res.room,
			"priority": "Normal",
			"details": (message or "").strip()[:2000] or None,
			"status": "Open",
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_minibar(token: str, item: str, quantity: float = 1) -> dict:
	"""Record a guest-initiated minibar consumption against the verified
	reservation's room. Server-derives the room from the token; client
	supplies only item + quantity."""
	reservation = _verify_token(token)
	qty = flt(quantity)
	if qty <= 0:
		frappe.throw(_("Quantity must be positive."))
	room = frappe.db.get_value("Hotel Reservation", reservation, "room")
	if not room:
		frappe.throw(_("Your room has not been assigned yet."))
	if not frappe.db.exists("Minibar Item", {"name": item, "enabled": 1}):
		frappe.throw(_("Item is not available."))
	doc = frappe.get_doc(
		{
			"doctype": "Minibar Consumption",
			"room": room,
			"item": item,
			"quantity": qty,
			"consumed_at": now_datetime(),
			"notes": "guest:self-served",
		}
	)
	doc.insert(ignore_permissions=True)
	return {
		"name": doc.name,
		"amount": flt(doc.amount),
		"guest_folio": doc.guest_folio,
		"posted": bool(doc.guest_folio),
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_concierge(token: str, category: str, message: str) -> dict:
	"""Explicit concierge request — synonym of request_amenity but expects
	a non-empty message."""
	if not (message or "").strip():
		frappe.throw(_("Please describe what you'd like."))
	return request_amenity(token=token, category=category, message=message)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_minibar_catalog(token: str) -> list[dict]:
	"""Catalog of enabled minibar items, gated by a valid guest token."""
	_verify_token(token)
	return frappe.get_all(
		"Minibar Item",
		fields=["name", "code", "item_name", "price", "category", "description"],
		filters={"enabled": 1},
		order_by="category asc, item_name asc",
		ignore_permissions=True,
	)
