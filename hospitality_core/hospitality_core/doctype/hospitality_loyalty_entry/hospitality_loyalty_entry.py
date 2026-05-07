import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HospitalityLoyaltyEntry(Document):
	def validate(self):
		if flt(self.points) < 0:
			frappe.throw(_("Points must be non-negative — use entry_type to indicate direction."))

	def on_update(self):
		_recompute_account(self.guest)

	def on_trash(self):
		_recompute_account(self.guest)


def _recompute_account(guest: str) -> None:
	if not guest:
		return
	if not frappe.db.exists("Hospitality Loyalty Account", guest):
		account = frappe.new_doc("Hospitality Loyalty Account")
		account.guest = guest
	else:
		account = frappe.get_doc("Hospitality Loyalty Account", guest)
	account.recompute_from_entries()
	account.save(ignore_permissions=True)
