"""Lost and Found APIs.

The DocType already exists (Lost and Found Item) with status workflow
Found / Claimed / Disposed. This module adds the operational endpoints
plus optional reception scoping via the found_location.hotel_reception
join.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import nowdate


@frappe.whitelist()
def log_item(
	item_name: str,
	found_date: str | None = None,
	found_location: str | None = None,
	finder: str | None = None,
) -> str:
	if not frappe.has_permission("Lost and Found Item", "create"):
		frappe.throw(_("Not authorised to log lost and found items."))

	doc = frappe.get_doc(
		{
			"doctype": "Lost and Found Item",
			"item_name": item_name,
			"found_date": found_date or nowdate(),
			"found_location": found_location,
			"finder": finder,
			"status": "Found",
		}
	)
	doc.insert()
	return doc.name


@frappe.whitelist()
def claim_item(name: str, claimant_info: str, claimed_date: str | None = None) -> dict:
	if not claimant_info:
		frappe.throw(_("Claimant info is required."))
	doc = frappe.get_doc("Lost and Found Item", name)
	doc.check_permission("write")
	if doc.status != "Found":
		frappe.throw(_("Item is already {0}.").format(doc.status))
	doc.status = "Claimed"
	doc.claimant_info = claimant_info
	doc.claimed_date = claimed_date or nowdate()
	doc.save()
	return {"name": doc.name, "status": doc.status, "claimed_date": str(doc.claimed_date)}


@frappe.whitelist()
def dispose_item(name: str, reason: str | None = None) -> dict:
	doc = frappe.get_doc("Lost and Found Item", name)
	doc.check_permission("write")
	if doc.status == "Disposed":
		return {"name": doc.name, "status": doc.status}
	doc.status = "Disposed"
	if reason:
		doc.claimant_info = (doc.claimant_info or "") + f"\nDisposed: {reason}"
	doc.save()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def list_open_items(hotel_reception: str | None = None, limit: int = 200) -> list[dict]:
	if hotel_reception:
		rows = frappe.db.sql(
			"""
			SELECT lf.name, lf.item_name, lf.found_date, lf.found_location,
			       lf.finder, lf.status, room.hotel_reception
			FROM `tabLost and Found Item` lf
			LEFT JOIN `tabHotel Room` room ON lf.found_location = room.name
			WHERE lf.status = 'Found'
			AND (room.hotel_reception = %(rec)s OR lf.found_location IS NULL)
			ORDER BY lf.found_date DESC
			LIMIT %(limit)s
			""",
			{"rec": hotel_reception, "limit": int(limit)},
			as_dict=True,
		)
		return rows
	return frappe.get_all(
		"Lost and Found Item",
		fields=["name", "item_name", "found_date", "found_location", "finder", "status"],
		filters={"status": "Found"},
		order_by="found_date desc",
		limit=int(limit),
	)


@frappe.whitelist()
def auto_dispose_aged_items(days: int = 90) -> dict:
	"""Operational helper: dispose items still in Found status after N days.
	Call manually or wire into a scheduled task."""
	from frappe.utils import add_days

	cutoff = add_days(nowdate(), -int(days))
	rows = frappe.get_all(
		"Lost and Found Item",
		filters={"status": "Found", "found_date": ["<", cutoff]},
		pluck="name",
	)
	count = 0
	for name in rows:
		try:
			doc = frappe.get_doc("Lost and Found Item", name)
			doc.status = "Disposed"
			doc.claimant_info = (doc.claimant_info or "") + f"\nAuto-disposed after {days}d unclaimed"
			doc.save(ignore_permissions=True)
			count += 1
		except Exception:
			frappe.log_error(title=f"L&F auto-dispose failed for {name}")
	return {"disposed": count, "cutoff": cutoff}
