import frappe
from frappe import _
from frappe.model.document import Document


class GuestPreference(Document):
	def validate(self):
		if self.preferred_temperature is not None:
			temp = int(self.preferred_temperature)
			if temp < 16 or temp > 30:
				frappe.throw(_("Preferred temperature must be between 16°C and 30°C."))
		if self.do_not_disturb_window:
			# Soft validate format like "22:00-08:00" — don't reject, just normalise spaces
			self.do_not_disturb_window = self.do_not_disturb_window.strip()
