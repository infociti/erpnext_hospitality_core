import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class HousekeepingTask(Document):
	def before_insert(self):
		if not self.requested_at:
			self.requested_at = now_datetime()
		if not self.status:
			self.status = "Pending"

	def validate(self):
		if self.status == "Assigned" and not self.assigned_to:
			frappe.throw(_("Cannot mark task as Assigned without an assignee."))
		if self.status == "In Progress" and not self.started_at:
			self.started_at = now_datetime()
		if self.status == "Completed" and not self.completed_at:
			self.completed_at = now_datetime()
		if self.status == "Completed" and self.started_at is None:
			self.started_at = self.completed_at

	def on_update(self):
		# Reflect cleaning state on the room
		if self.task_type in ("Cleaning", "Deep Clean") and self.room:
			if self.status == "Completed":
				active_res = frappe.db.exists(
					"Hotel Reservation",
					{"room": self.room, "status": "Checked In"},
				)
				new_status = "Occupied" if active_res else "Available"
				frappe.db.set_value("Hotel Room", self.room, "status", new_status)
			elif self.status in ("Pending", "Assigned", "In Progress"):
				current = frappe.db.get_value("Hotel Room", self.room, "status")
				if current == "Available":
					frappe.db.set_value("Hotel Room", self.room, "status", "Dirty")
