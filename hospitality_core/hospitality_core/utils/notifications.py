"""Hospitality notification dispatch.

All sends are non-blocking (failures swallowed and reported via
frappe.log_error) so a notification problem can never block the primary
business action that triggered it.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import strip_html


def _render(template_name: str, ctx: dict[str, Any]) -> tuple[str, str] | None:
	if not frappe.db.exists("Email Template", template_name):
		return None
	tpl = frappe.get_doc("Email Template", template_name)
	subject = frappe.render_template(tpl.subject or "", ctx) if tpl.subject else ""
	body = frappe.render_template(tpl.response or "", ctx)
	return subject.strip(), body


def _guest_email(guest: str | None) -> str | None:
	if not guest:
		return None
	return frappe.db.get_value("Guest", guest, "email_id")


def _guest_name(guest: str | None) -> str:
	if not guest:
		return _("Guest")
	return frappe.db.get_value("Guest", guest, "full_name") or guest


def _send(to: str, subject: str, body: str) -> None:
	try:
		frappe.sendmail(recipients=[to], subject=subject, message=body, now=False)
	except Exception:
		frappe.log_error(title=f"Hospitality notification send failed: {subject!r}")


def send_booking_confirmation(reservation_doc) -> None:
	to = _guest_email(getattr(reservation_doc, "guest", None))
	if not to:
		return
	rendered = _render(
		"Hospitality - Booking Confirmation",
		{"doc": reservation_doc, "guest_name": _guest_name(reservation_doc.guest), "frappe": frappe},
	)
	if rendered:
		_send(to, *rendered)


def send_checkin_welcome(reservation_doc) -> None:
	to = _guest_email(getattr(reservation_doc, "guest", None))
	if not to:
		return
	rendered = _render(
		"Hospitality - Check-in Welcome",
		{"doc": reservation_doc, "guest_name": _guest_name(reservation_doc.guest), "frappe": frappe},
	)
	if rendered:
		_send(to, *rendered)


def send_checkout_thanks(reservation_doc) -> None:
	to = _guest_email(getattr(reservation_doc, "guest", None))
	if not to:
		return
	rendered = _render(
		"Hospitality - Check-out Thanks",
		{"doc": reservation_doc, "guest_name": _guest_name(reservation_doc.guest), "frappe": frappe},
	)
	if rendered:
		_send(to, *rendered)


def send_payment_receipt(payment_doc) -> None:
	# Resolve guest from the linked Guest Folio (if any)
	guest = None
	for ref in (payment_doc.references or []):
		if ref.get("reference_doctype") == "Guest Folio":
			guest = frappe.db.get_value("Guest Folio", ref.get("reference_name"), "guest")
			if guest:
				break
	to = _guest_email(guest)
	if not to:
		return
	rendered = _render(
		"Hospitality - Payment Receipt",
		{"doc": payment_doc, "guest_name": _guest_name(guest), "frappe": frappe},
	)
	if rendered:
		_send(to, *rendered)


# ---- doc_event hook glue --------------------------------------------------

def hook_reservation_status(doc, method=None) -> None:
	"""Dispatch the right email based on the reservation's current status."""
	try:
		status = getattr(doc, "status", None)
		if method == "on_submit":
			send_booking_confirmation(doc)
		elif method == "on_update_after_submit":
			if status == "Checked In":
				send_checkin_welcome(doc)
			elif status == "Checked Out":
				send_checkout_thanks(doc)
	except Exception:
		frappe.log_error(title="Hospitality reservation notification dispatch failed")


def hook_payment_receipt(doc, method=None) -> None:
	try:
		send_payment_receipt(doc)
	except Exception:
		frappe.log_error(title="Hospitality payment receipt dispatch failed")


# ---- after_migrate -------------------------------------------------------

_TEMPLATES = [
	{
		"name": "Hospitality - Booking Confirmation",
		"subject": "Booking confirmed — {{ doc.name }}",
		"response": (
			"<p>Dear {{ guest_name or 'Guest' }},</p>"
			"<p>Your booking is confirmed.</p>"
			"<table>"
			"<tr><td><strong>Reservation</strong></td><td>{{ doc.name }}</td></tr>"
			"<tr><td><strong>Arrival</strong></td><td>{{ doc.arrival_date }}</td></tr>"
			"<tr><td><strong>Departure</strong></td><td>{{ doc.departure_date }}</td></tr>"
			"<tr><td><strong>Room Type</strong></td><td>{{ doc.room_type }}</td></tr>"
			"<tr><td><strong>Room</strong></td><td>{{ doc.room or 'Will be assigned at check-in' }}</td></tr>"
			"</table>"
			"<p>We look forward to welcoming you.</p><p>— Front Desk</p>"
		),
	},
	{
		"name": "Hospitality - Check-in Welcome",
		"subject": "Welcome — Room {{ doc.room }}",
		"response": (
			"<p>Dear {{ guest_name or 'Guest' }},</p>"
			"<p>Welcome! You are checked in to room <strong>{{ doc.room }}</strong>.</p>"
			"<p>Wifi, room service, and local information are available from your in-room TV or via the front desk at any time.</p>"
			"<p>Departure: {{ doc.departure_date }}</p>"
			"<p>Enjoy your stay,</p><p>— Front Desk</p>"
		),
	},
	{
		"name": "Hospitality - Check-out Thanks",
		"subject": "Thank you for staying with us",
		"response": (
			"<p>Dear {{ guest_name or 'Guest' }},</p>"
			"<p>Thank you for staying with us. We hope you had a comfortable stay in room {{ doc.room }}.</p>"
			"<p>We would love to see you again.</p><p>— Front Desk</p>"
		),
	},
	{
		"name": "Hospitality - Payment Receipt",
		"subject": "Payment received — {{ doc.name }}",
		"response": (
			"<p>Dear {{ guest_name or 'Guest' }},</p>"
			"<p>We have received your payment of <strong>{{ doc.paid_amount }}</strong>.</p>"
			"<table>"
			"<tr><td><strong>Reference</strong></td><td>{{ doc.name }}</td></tr>"
			"<tr><td><strong>Date</strong></td><td>{{ doc.posting_date }}</td></tr>"
			"<tr><td><strong>Mode</strong></td><td>{{ doc.mode_of_payment }}</td></tr>"
			"</table>"
			"<p>Thank you,</p><p>— Front Desk</p>"
		),
	},
]


def ensure_email_templates() -> None:
	"""Idempotently install/update the four hospitality email templates.
	Wired to after_migrate; safe to re-run."""
	for t in _TEMPLATES:
		try:
			if frappe.db.exists("Email Template", t["name"]):
				existing = frappe.get_doc("Email Template", t["name"])
				if existing.subject == t["subject"] and existing.response == t["response"]:
					continue
				existing.subject = t["subject"]
				existing.response = t["response"]
				existing.use_html = 1
				existing.save(ignore_permissions=True)
			else:
				frappe.get_doc(
					{
						"doctype": "Email Template",
						"name": t["name"],
						"subject": t["subject"],
						"response": t["response"],
						"use_html": 1,
					}
				).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(title=f"Hospitality template seed failed: {t['name']}")
	frappe.db.commit()
