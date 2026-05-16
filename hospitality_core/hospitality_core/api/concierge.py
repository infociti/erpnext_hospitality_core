"""Concierge Request APIs.

Endpoints for the front-desk and concierge dashboards. Status transitions
auto-stamp started_at and resolved_at on the document via the controller.
"""

from __future__ import annotations

import frappe
from frappe import _


@frappe.whitelist()
def open_request(
	subject: str,
	category: str = "Other",
	guest: str | None = None,
	room: str | None = None,
	priority: str = "Normal",
	details: str | None = None,
	due_at: str | None = None,
) -> str:
	if not frappe.has_permission("Concierge Request", "create"):
		frappe.throw(_("Not authorised to create concierge requests."))

	doc = frappe.get_doc(
		{
			"doctype": "Concierge Request",
			"subject": subject,
			"category": category,
			"guest": guest,
			"room": room,
			"priority": priority,
			"details": details,
			"due_at": due_at,
			"status": "Open",
		}
	)
	doc.insert()
	return doc.name


@frappe.whitelist()
def list_requests(
	hotel_reception: str | None = None,
	status: str | None = None,
	assigned_to: str | None = None,
	limit: int = 200,
) -> list[dict]:
	filters = {}
	if hotel_reception:
		filters["hotel_reception"] = hotel_reception
	if status:
		filters["status"] = status
	if assigned_to:
		filters["assigned_to"] = assigned_to
	return frappe.get_all(
		"Concierge Request",
		fields=[
			"name",
			"guest",
			"room",
			"category",
			"priority",
			"status",
			"assigned_to",
			"subject",
			"due_at",
			"started_at",
			"resolved_at",
			"creation",
		],
		filters=filters,
		order_by="FIELD(priority, 'Urgent', 'High', 'Normal', 'Low'), creation asc",
		limit=int(limit),
	)


@frappe.whitelist()
def acknowledge(name: str, assignee: str | None = None) -> dict:
	doc = frappe.get_doc("Concierge Request", name)
	doc.check_permission("write")
	if assignee:
		doc.assigned_to = assignee
	if not doc.assigned_to:
		doc.assigned_to = frappe.session.user
	doc.status = "Acknowledged"
	doc.save()
	return {"status": doc.status, "assigned_to": doc.assigned_to}


@frappe.whitelist()
def start(name: str) -> dict:
	doc = frappe.get_doc("Concierge Request", name)
	doc.check_permission("write")
	doc.status = "In Progress"
	doc.save()
	return {"status": doc.status, "started_at": str(doc.started_at)}


@frappe.whitelist()
def resolve(name: str, resolution_notes: str | None = None) -> dict:
	doc = frappe.get_doc("Concierge Request", name)
	doc.check_permission("write")
	doc.status = "Resolved"
	if resolution_notes:
		doc.resolution_notes = resolution_notes
	doc.save()
	return {"status": doc.status, "resolved_at": str(doc.resolved_at)}


@frappe.whitelist()
def cancel(name: str, reason: str | None = None) -> dict:
	doc = frappe.get_doc("Concierge Request", name)
	doc.check_permission("write")
	doc.status = "Cancelled"
	if reason:
		doc.resolution_notes = (doc.resolution_notes or "") + f"\nCancelled: {reason}"
	doc.save()
	return {"status": doc.status}
