"""Hospitality audit logging.

Use ``log_event`` from anywhere — controllers, hooks, scheduled tasks.
Failures are swallowed (and reported via frappe.log_error) so a logging
problem never blocks the primary action.
"""

from __future__ import annotations

import json
from typing import Any

import frappe


_MAX_PAYLOAD = 4000


def _safe_payload(payload: Any) -> str | None:
	if payload is None:
		return None
	try:
		text = json.dumps(payload, default=str, ensure_ascii=False)
	except (TypeError, ValueError):
		text = str(payload)
	return text[:_MAX_PAYLOAD]


def _client_ip() -> str | None:
	try:
		return getattr(frappe.local, "request_ip", None)
	except Exception:
		return None


def log_event(
	event_type: str,
	*,
	ref_doctype: str | None = None,
	ref_name: str | None = None,
	hotel_reception: str | None = None,
	payload: Any = None,
	user: str | None = None,
) -> None:
	"""Insert a Hospitality Audit Log entry. Never raises."""
	try:
		doc = frappe.get_doc(
			{
				"doctype": "Hospitality Audit Log",
				"event_type": event_type[:140],
				"ref_doctype": ref_doctype,
				"ref_name": ref_name[:140] if ref_name else None,
				"hotel_reception": hotel_reception,
				"user": user or frappe.session.user,
				"ip_address": _client_ip(),
				"payload": _safe_payload(payload),
			}
		)
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Hospitality audit log insert failed")


def hook_log_reservation(doc, method: str | None = None) -> None:
	if not doc:
		return
	event = f"reservation.{method or 'event'}"
	log_event(
		event,
		ref_doctype=doc.doctype,
		ref_name=doc.name,
		hotel_reception=getattr(doc, "hotel_reception", None),
		payload={
			"status": getattr(doc, "status", None),
			"guest": getattr(doc, "guest", None),
			"room": getattr(doc, "room", None),
			"arrival_date": str(getattr(doc, "arrival_date", "") or ""),
			"departure_date": str(getattr(doc, "departure_date", "") or ""),
		},
	)


def hook_log_folio(doc, method: str | None = None) -> None:
	if not doc:
		return
	event = f"folio.{method or 'event'}"
	log_event(
		event,
		ref_doctype=doc.doctype,
		ref_name=doc.name,
		hotel_reception=getattr(doc, "hotel_reception", None),
		payload={
			"status": getattr(doc, "status", None),
			"reservation": getattr(doc, "reservation", None),
			"outstanding_balance": float(getattr(doc, "outstanding_balance", 0) or 0),
		},
	)


def hook_log_folio_transaction(doc, method: str | None = None) -> None:
	if not doc:
		return
	event = f"folio_transaction.{method or 'event'}"
	parent_reception = None
	if getattr(doc, "parent", None):
		parent_reception = frappe.db.get_value("Guest Folio", doc.parent, "hotel_reception")
	log_event(
		event,
		ref_doctype=doc.doctype,
		ref_name=doc.name,
		hotel_reception=parent_reception,
		payload={
			"parent_folio": getattr(doc, "parent", None),
			"transaction_type": getattr(doc, "transaction_type", None),
			"amount": float(getattr(doc, "amount", 0) or 0),
			"is_void": int(getattr(doc, "is_void", 0) or 0),
			"posting_date": str(getattr(doc, "posting_date", "") or ""),
		},
	)


def hook_log_expense(doc, method: str | None = None) -> None:
	if not doc:
		return
	event = f"expense.{method or 'event'}"
	log_event(
		event,
		ref_doctype=doc.doctype,
		ref_name=doc.name,
		hotel_reception=getattr(doc, "hotel_reception", None),
		payload={
			"expense_category": getattr(doc, "expense_category", None),
			"grand_total": float(getattr(doc, "grand_total", 0) or 0),
			"docstatus": int(getattr(doc, "docstatus", 0) or 0),
		},
	)


def hook_log_payment(doc, method: str | None = None) -> None:
	if not doc:
		return
	event = f"payment.{method or 'event'}"
	log_event(
		event,
		ref_doctype=doc.doctype,
		ref_name=doc.name,
		payload={
			"payment_type": getattr(doc, "payment_type", None),
			"paid_amount": float(getattr(doc, "paid_amount", 0) or 0),
			"mode_of_payment": getattr(doc, "mode_of_payment", None),
			"docstatus": int(getattr(doc, "docstatus", 0) or 0),
		},
	)
