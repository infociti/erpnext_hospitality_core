"""Mini-bar APIs.

Records consumption, auto-posting to the room's active Guest Folio. Voids
mark the consumption inactive and flip the underlying folio transaction's
is_void flag (the GL entry hook handles the reversal).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, now_datetime


@frappe.whitelist()
def list_catalog(category: str | None = None, enabled_only: int = 1) -> list[dict]:
	filters = {}
	if enabled_only:
		filters["enabled"] = 1
	if category:
		filters["category"] = category
	return frappe.get_all(
		"Minibar Item",
		fields=["name", "code", "item_name", "price", "category", "description"],
		filters=filters,
		order_by="category asc, item_name asc",
	)


@frappe.whitelist()
def record_consumption(
	room: str,
	item: str,
	quantity: float = 1,
	consumed_at: str | None = None,
	notes: str | None = None,
) -> dict:
	if not frappe.has_permission("Minibar Consumption", "create"):
		frappe.throw(_("Not authorised to record minibar consumption."))
	if flt(quantity) <= 0:
		frappe.throw(_("Quantity must be positive."))

	doc = frappe.get_doc(
		{
			"doctype": "Minibar Consumption",
			"room": room,
			"item": item,
			"quantity": quantity,
			"consumed_at": consumed_at or now_datetime(),
			"notes": notes,
		}
	)
	doc.insert()
	return {
		"name": doc.name,
		"amount": flt(doc.amount),
		"guest_folio": doc.guest_folio,
		"posted": bool(doc.guest_folio),
	}


@frappe.whitelist()
def list_consumption(
	room: str | None = None,
	guest_folio: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	limit: int = 200,
) -> list[dict]:
	filters = {}
	if room:
		filters["room"] = room
	if guest_folio:
		filters["guest_folio"] = guest_folio
	if from_date or to_date:
		bounds = []
		if from_date:
			bounds.append([">=", from_date])
		if to_date:
			bounds.append(["<=", to_date])
		filters["consumed_at"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31"]]
	return frappe.get_all(
		"Minibar Consumption",
		fields=[
			"name",
			"room",
			"item",
			"quantity",
			"unit_price",
			"amount",
			"consumed_at",
			"guest_folio",
			"is_voided",
			"void_reason",
		],
		filters=filters,
		order_by="consumed_at desc",
		limit=int(limit),
	)


@frappe.whitelist()
def void_consumption(name: str, reason: str) -> dict:
	if not reason:
		frappe.throw(_("A reason is required to void minibar consumption."))
	doc = frappe.get_doc("Minibar Consumption", name)
	doc.check_permission("write")
	if doc.is_voided:
		return {"name": doc.name, "is_voided": 1}

	doc.is_voided = 1
	doc.void_reason = reason
	doc.save()

	# If we posted to a folio, mark that row voided (the GL hook reverses).
	if doc.guest_folio and doc.folio_transaction_idx:
		folio = frappe.get_doc("Guest Folio", doc.guest_folio)
		for row in folio.transactions:
			if (
				row.idx == doc.folio_transaction_idx
				and row.reference_doctype == "Minibar Consumption"
				and row.reference_name == doc.name
			):
				row.is_void = 1
				row.void_reason = reason
				break
		folio.save(ignore_permissions=True)
	return {"name": doc.name, "is_voided": 1}
