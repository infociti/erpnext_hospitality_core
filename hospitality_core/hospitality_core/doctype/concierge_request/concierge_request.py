import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class ConciergeRequest(Document):
	def validate(self):
		if self.status == "In Progress" and not self.started_at:
			self.started_at = now_datetime()
		if self.status == "Resolved" and not self.resolved_at:
			self.resolved_at = now_datetime()
		if self.status == "Acknowledged" and self.assigned_to is None:
			frappe.throw(_("Set an assignee before acknowledging."))

	def on_update(self):
		if self.has_value_changed("status") and self.status == "Resolved":
			frappe.publish_realtime(
				event="concierge_request_resolved",
				message={"name": self.name, "guest": self.guest, "room": self.room},
				user=self.owner,
			)
