import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime


class MinibarConsumption(Document):
	def before_insert(self):
		if not self.consumed_at:
			self.consumed_at = now_datetime()
		if not self.staff_user:
			self.staff_user = frappe.session.user
		if not self.unit_price and self.item:
			self.unit_price = frappe.db.get_value("Minibar Item", self.item, "price")

	def validate(self):
		if flt(self.quantity) <= 0:
			frappe.throw(_("Quantity must be positive."))
		self.amount = flt(self.quantity) * flt(self.unit_price or 0)

	def after_insert(self):
		"""Auto-post the consumption to the room's active Guest Folio."""
		if self.is_voided or not self.room:
			return

		folio_name = _resolve_active_folio_for_room(self.room)
		if not folio_name:
			# No active folio — leave consumption recorded but unposted.
			frappe.msgprint(
				_("No open Guest Folio for room {0}; consumption recorded but unposted.").format(self.room)
			)
			return

		folio = frappe.get_doc("Guest Folio", folio_name)
		row = folio.append(
			"transactions",
			{
				"posting_date": getdate(self.consumed_at),
				"description": f"Minibar: {frappe.db.get_value('Minibar Item', self.item, 'item_name') or self.item} x{self.quantity}",
				"qty": self.quantity,
				"amount": self.amount,
				"bill_to": "Guest",
				"is_void": 0,
				"reference_doctype": "Minibar Consumption",
				"reference_name": self.name,
			},
		)
		folio.save(ignore_permissions=True)
		# row.idx is set after save
		self.db_set("guest_folio", folio.name, update_modified=False)
		self.db_set("folio_transaction_idx", row.idx, update_modified=False)


def _resolve_active_folio_for_room(room: str) -> str | None:
	"""Find the most recent OPEN Guest Folio whose reservation currently
	occupies this room."""
	rows = frappe.db.sql(
		"""
		SELECT gf.name
		FROM `tabGuest Folio` gf
		INNER JOIN `tabHotel Reservation` res ON gf.reservation = res.name
		WHERE res.room = %s
		AND res.status = 'Checked In'
		AND gf.status NOT IN ('Closed', 'Cancelled')
		ORDER BY gf.creation DESC
		LIMIT 1
		""",
		(room,),
	)
	return rows[0][0] if rows else None
