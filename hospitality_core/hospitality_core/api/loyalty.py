"""Loyalty program APIs.

Points are stored as ledger entries (Hospitality Loyalty Entry) and
aggregated into per-guest Loyalty Account documents. The Account is
recomputed automatically from the Entry on_update hook — never write
directly to the Account's balance.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


_DEFAULT_EARN_PER_UNIT = 0.10  # 10% of stay revenue accrues as points


@frappe.whitelist()
def get_balance(guest: str) -> dict:
	if not frappe.db.exists("Guest", guest):
		frappe.throw(_("Guest {0} does not exist.").format(guest))
	if not frappe.db.exists("Hospitality Loyalty Account", guest):
		return {
			"guest": guest,
			"tier": "Standard",
			"points_balance": 0.0,
			"lifetime_points": 0.0,
			"lifetime_revenue": 0.0,
			"stays_count": 0,
		}
	doc = frappe.get_doc("Hospitality Loyalty Account", guest)
	doc.check_permission("read")
	return {
		"guest": guest,
		"tier": doc.tier,
		"points_balance": flt(doc.points_balance),
		"lifetime_points": flt(doc.lifetime_points),
		"lifetime_revenue": flt(doc.lifetime_revenue),
		"stays_count": int(doc.stays_count or 0),
		"last_activity_at": doc.last_activity_at,
	}


@frappe.whitelist()
def earn_points(
	guest: str,
	points: float,
	reservation: str | None = None,
	folio: str | None = None,
	amount_basis: float | None = None,
	notes: str | None = None,
) -> str:
	if not frappe.has_permission("Hospitality Loyalty Entry", "create"):
		frappe.throw(_("Not authorised to write loyalty entries."))
	doc = frappe.get_doc(
		{
			"doctype": "Hospitality Loyalty Entry",
			"guest": guest,
			"entry_type": "Earn",
			"points": flt(points),
			"reservation": reservation,
			"folio": folio,
			"amount_basis": amount_basis,
			"notes": notes,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def redeem_points(
	guest: str,
	points: float,
	reservation: str | None = None,
	folio: str | None = None,
	notes: str | None = None,
) -> str:
	if flt(points) <= 0:
		frappe.throw(_("Redemption must be positive."))
	balance = flt(get_balance(guest).get("points_balance"))
	if balance < flt(points):
		frappe.throw(_("Insufficient balance: {0} available, {1} requested.").format(balance, points))
	doc = frappe.get_doc(
		{
			"doctype": "Hospitality Loyalty Entry",
			"guest": guest,
			"entry_type": "Redeem",
			"points": flt(points),
			"reservation": reservation,
			"folio": folio,
			"notes": notes,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def adjust_balance(guest: str, points: float, reason: str) -> str:
	"""Manager-only adjustment (positive or negative)."""
	if not ({"Hospitality Manager", "System Manager"} & set(frappe.get_roles())):
		frappe.throw(_("Only managers can adjust loyalty balances."))
	if not reason:
		frappe.throw(_("A reason is required for adjustments."))
	doc = frappe.get_doc(
		{
			"doctype": "Hospitality Loyalty Entry",
			"guest": guest,
			"entry_type": "Adjustment",
			"points": abs(flt(points)),
			"notes": f"Adjustment: {reason}",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def list_entries(guest: str, limit: int = 50) -> list[dict]:
	return frappe.get_all(
		"Hospitality Loyalty Entry",
		fields=["name", "entry_type", "points", "reservation", "folio", "amount_basis", "creation", "notes"],
		filters={"guest": guest},
		order_by="creation desc",
		limit=int(limit),
	)


# ---- doc_event hook --------------------------------------------------------

def hook_on_reservation_checkout(doc, method=None) -> None:
	"""Auto-accrue points on guest check-out, scaled to folio total."""
	if not doc or getattr(doc, "status", None) != "Checked Out":
		return
	guest = getattr(doc, "guest", None)
	if not guest:
		return

	# De-dup: don't earn twice for the same reservation
	if frappe.db.exists(
		"Hospitality Loyalty Entry",
		{"guest": guest, "reservation": doc.name, "entry_type": "Earn"},
	):
		return

	# Total spend from the closed folio (if any)
	folio_name = frappe.db.get_value(
		"Guest Folio",
		{"reservation": doc.name},
		"name",
		order_by="creation desc",
	)
	revenue = 0.0
	if folio_name:
		revenue = (
			frappe.db.sql(
				"""
				SELECT COALESCE(SUM(amount), 0)
				FROM `tabFolio Transaction`
				WHERE parent = %s AND is_void = 0
				AND (reference_doctype != 'Payment Entry' OR reference_doctype IS NULL)
				""",
				(folio_name,),
			)[0][0]
			or 0
		)

	if flt(revenue) <= 0:
		return

	points = flt(revenue) * _DEFAULT_EARN_PER_UNIT
	try:
		entry = frappe.get_doc(
			{
				"doctype": "Hospitality Loyalty Entry",
				"guest": guest,
				"entry_type": "Earn",
				"points": points,
				"reservation": doc.name,
				"folio": folio_name,
				"amount_basis": revenue,
				"notes": f"Auto-accrual on check-out (rate {_DEFAULT_EARN_PER_UNIT})",
			}
		)
		entry.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Loyalty auto-accrual failed")
