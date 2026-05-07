import frappe
from frappe import _
from frappe.model.document import Document


class MinibarItem(Document):
	def validate(self):
		if self.price is None or self.price < 0:
			frappe.throw(_("Unit price must be zero or positive."))
