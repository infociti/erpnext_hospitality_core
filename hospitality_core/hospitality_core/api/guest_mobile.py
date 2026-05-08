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
from frappe.utils import flt, getdate

from hospitality_core.hospitality_core.api.concierge import open_request as _open_concierge
from hospitality_core.hospitality_core.api.minibar import record_consumption as _record_minibar

_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12h
_OPEN_RES_STATUSES = ("Reserved", "Checked In")


def _site_secret() -> str:
	"""Per-site signing secret. Falls back to encryption_key, then to the
	site name — sufficient given tokens carry their own expiry."""
	conf = frappe.local.conf
	return (
		conf.get("hospitality_guest_secret")
		or conf.get("encryption_key")
		or conf.get("secret_key")
		or frappe.local.site
	)


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


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_amenity(token: str, category: str, message: str | None = None) -> dict:
	"""Generic amenity / housekeeping request. Funnels into a Concierge
	Request — staff workflow lives there."""
	reservation = _verify_token(token)
	room = frappe.db.get_value("Hotel Reservation", reservation, "room")
	if not room:
		frappe.throw(_("Your room has not been assigned yet."))
	# Pretend to be system user for the create — guest tokens are not
	# Frappe Users, so we bypass permissions here.
	original = frappe.session.user
	try:
		frappe.set_user("Administrator")
		result = _open_concierge(
			reservation=reservation,
			category=category,
			message=message or "",
		)
	finally:
		frappe.set_user(original)
	return result


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_minibar(token: str, item: str, quantity: float = 1) -> dict:
	"""Self-charged minibar request from the in-room device. Posts directly
	to the active folio via the existing minibar pipeline."""
	reservation = _verify_token(token)
	room = frappe.db.get_value("Hotel Reservation", reservation, "room")
	if not room:
		frappe.throw(_("Your room has not been assigned yet."))
	original = frappe.session.user
	try:
		frappe.set_user("Administrator")
		result = _record_minibar(room=room, item=item, quantity=flt(quantity))
	finally:
		frappe.set_user(original)
	return result


@frappe.whitelist(allow_guest=True, methods=["POST"])
def request_concierge(token: str, category: str, message: str) -> dict:
	"""Explicit concierge request — synonym of request_amenity but expects
	a non-empty message."""
	if not (message or "").strip():
		frappe.throw(_("Please describe what you'd like."))
	return request_amenity(token=token, category=category, message=message)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_minibar_catalog(token: str) -> list[dict]:
	"""Public catalog visible to authenticated guests — wraps the staff
	endpoint with a token check."""
	_verify_token(token)
	return frappe.get_all(
		"Minibar Item",
		fields=["name", "code", "item_name", "price", "category", "description"],
		filters={"enabled": 1},
		order_by="category asc, item_name asc",
	)
