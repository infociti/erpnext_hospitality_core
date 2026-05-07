"""Housekeeping task APIs.

Whitelisted endpoints for staff dashboards and the Housekeeping page:
* ``list_tasks`` — board-style list filtered by status / assignee / reception
* ``create_task`` — manual task creation (turndown, deep clean, inspection)
* ``assign_task`` — set assignee + move to "Assigned"
* ``start_task`` — record started_at + move to "In Progress"
* ``complete_task`` — record completed_at + room status flip via on_update
* ``cancel_task``
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime


@frappe.whitelist()
def list_tasks(
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
		"Housekeeping Task",
		fields=[
			"name",
			"room",
			"hotel_reception",
			"task_type",
			"priority",
			"status",
			"assigned_to",
			"requested_at",
			"started_at",
			"completed_at",
			"parent_reservation",
		],
		filters=filters,
		order_by="priority desc, requested_at asc",
		limit=int(limit),
	)


@frappe.whitelist()
def create_task(
	room: str,
	task_type: str = "Cleaning",
	priority: str = "Normal",
	notes: str | None = None,
	parent_reservation: str | None = None,
	assigned_to: str | None = None,
) -> str:
	if not frappe.has_permission("Housekeeping Task", "create"):
		frappe.throw(_("Not authorised to create housekeeping tasks."))

	doc = frappe.get_doc(
		{
			"doctype": "Housekeeping Task",
			"room": room,
			"task_type": task_type,
			"priority": priority,
			"notes": notes,
			"parent_reservation": parent_reservation,
			"assigned_to": assigned_to,
			"status": "Assigned" if assigned_to else "Pending",
		}
	)
	doc.insert()
	return doc.name


@frappe.whitelist()
def assign_task(task: str, assignee: str) -> dict:
	doc = frappe.get_doc("Housekeeping Task", task)
	doc.check_permission("write")
	doc.assigned_to = assignee
	if doc.status == "Pending":
		doc.status = "Assigned"
	doc.save()
	return {"status": doc.status, "assigned_to": doc.assigned_to}


@frappe.whitelist()
def start_task(task: str) -> dict:
	doc = frappe.get_doc("Housekeeping Task", task)
	doc.check_permission("write")
	if doc.status not in ("Pending", "Assigned"):
		frappe.throw(_("Task is already {0}.").format(doc.status))
	doc.status = "In Progress"
	doc.started_at = now_datetime()
	if not doc.assigned_to:
		doc.assigned_to = frappe.session.user
	doc.save()
	return {"status": doc.status, "started_at": str(doc.started_at)}


@frappe.whitelist()
def complete_task(task: str, completion_notes: str | None = None) -> dict:
	doc = frappe.get_doc("Housekeeping Task", task)
	doc.check_permission("write")
	if doc.status == "Completed":
		return {"status": doc.status}
	doc.status = "Completed"
	doc.completed_at = now_datetime()
	if completion_notes:
		doc.completion_notes = completion_notes
	doc.save()
	return {"status": doc.status, "completed_at": str(doc.completed_at)}


@frappe.whitelist()
def cancel_task(task: str, reason: str | None = None) -> dict:
	doc = frappe.get_doc("Housekeeping Task", task)
	doc.check_permission("write")
	doc.status = "Cancelled"
	if reason:
		doc.completion_notes = (doc.completion_notes or "") + f"\nCancelled: {reason}"
	doc.save()
	return {"status": doc.status}


def hook_on_reservation_checkout(doc, method=None) -> None:
	"""Auto-create a cleaning task when a reservation is checked out."""
	if not doc or not getattr(doc, "room", None):
		return
	if getattr(doc, "status", None) != "Checked Out":
		return
	# Avoid duplicate cleaning tasks for the same checkout
	existing = frappe.db.exists(
		"Housekeeping Task",
		{
			"parent_reservation": doc.name,
			"task_type": "Cleaning",
			"status": ["in", ["Pending", "Assigned", "In Progress"]],
		},
	)
	if existing:
		return
	try:
		task = frappe.get_doc(
			{
				"doctype": "Housekeeping Task",
				"room": doc.room,
				"task_type": "Cleaning",
				"priority": "High",
				"status": "Pending",
				"parent_reservation": doc.name,
				"notes": _("Auto-created on guest check-out"),
			}
		)
		task.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Housekeeping auto-task failed")
