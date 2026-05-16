import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HospitalityLoyaltyAccount(Document):
	def validate(self):
		if flt(self.points_balance) < 0:
			frappe.throw(_("Points balance cannot be negative."))
		if flt(self.lifetime_points) < flt(self.points_balance):
			self.lifetime_points = self.points_balance

	def recompute_from_entries(self):
		"""Recalculate balance + lifetime totals from Loyalty Entries."""
		rows = frappe.db.sql(
			"""
			SELECT
				SUM(CASE WHEN entry_type='Earn' THEN points
				         WHEN entry_type='Adjustment' THEN points
				         WHEN entry_type='Redeem' THEN -points
				         WHEN entry_type='Expiry' THEN -points
				         ELSE 0 END) AS balance,
				SUM(CASE WHEN entry_type='Earn' THEN points ELSE 0 END) AS lifetime,
				SUM(amount_basis) AS revenue,
				COUNT(DISTINCT reservation) AS stays
			FROM `tabHospitality Loyalty Entry`
			WHERE guest = %s
			""",
			(self.guest,),
			as_dict=True,
		)
		summary = rows[0] if rows else {}
		self.points_balance = flt(summary.get("balance"))
		self.lifetime_points = flt(summary.get("lifetime"))
		self.lifetime_revenue = flt(summary.get("revenue"))
		self.stays_count = int(summary.get("stays") or 0)
		self.last_activity_at = frappe.db.get_value(
			"Hospitality Loyalty Entry",
			{"guest": self.guest},
			"creation",
			order_by="creation desc",
		)
		self._recompute_tier()

	def _recompute_tier(self):
		thresholds = [
			("VVIP", 50000),
			("Platinum", 20000),
			("Gold", 5000),
			("Silver", 1000),
		]
		lifetime = flt(self.lifetime_points)
		for tier, threshold in thresholds:
			if lifetime >= threshold:
				self.tier = tier
				return
		self.tier = "Standard"
