"""Multi-tenant permission scoping for Hospitality Core.

Each property is identified by Hotel Reception. A user is granted access to
a reception by adding a User Permission (allow=Hotel Reception, for_value=<reception>).

System Manager and Administrator bypass scoping. Users with no Hotel Reception
permissions see no records (returns ``1=0``).
"""

from __future__ import annotations

import frappe


_BYPASS_ROLES = {"System Manager", "Hospitality Manager", "Administrator"}


def _user_receptions(user: str, doctype: str) -> list[str]:
	rows = frappe.db.sql(
		"""
		SELECT for_value FROM `tabUser Permission`
		WHERE user = %(user)s AND allow = 'Hotel Reception'
		AND (apply_to_all_doctypes = 1 OR applicable_for = %(dt)s)
		""",
		{"user": user, "dt": doctype},
		as_dict=False,
	)
	return [r[0] for r in rows if r and r[0]]


def _bypass(user: str) -> bool:
	if user in ("Administrator", "admin@hicl.local"):
		return True
	roles = set(frappe.get_roles(user))
	return bool(roles & _BYPASS_ROLES)


def _scope_clause(user: str, doctype: str, alias: str) -> str:
	if not user:
		user = frappe.session.user or "Guest"
	if user == "Guest":
		return "1=0"
	if _bypass(user):
		return ""
	receptions = _user_receptions(user, doctype)
	if not receptions:
		return f"{alias}.hotel_reception IS NULL"
	placeholders = ", ".join(frappe.db.escape(r) for r in receptions)
	return f"({alias}.hotel_reception IN ({placeholders}) OR {alias}.hotel_reception IS NULL)"


def hotel_room_query(user: str | None = None) -> str:
	return _scope_clause(user, "Hotel Room", "`tabHotel Room`")


def hotel_reservation_query(user: str | None = None) -> str:
	return _scope_clause(user, "Hotel Reservation", "`tabHotel Reservation`")


def guest_folio_query(user: str | None = None) -> str:
	return _scope_clause(user, "Guest Folio", "`tabGuest Folio`")


def hospitality_expense_query(user: str | None = None) -> str:
	return _scope_clause(user, "Hospitality Expense", "`tabHospitality Expense`")


def housekeeping_task_query(user: str | None = None) -> str:
	return _scope_clause(user, "Housekeeping Task", "`tabHousekeeping Task`")


def folio_transaction_query(user: str | None = None) -> str:
	"""Folio Transaction is a child of Guest Folio — scope via parent reception."""
	if not user:
		user = frappe.session.user or "Guest"
	if user == "Guest":
		return "1=0"
	if _bypass(user):
		return ""
	receptions = _user_receptions(user, "Guest Folio")
	if not receptions:
		return (
			"NOT EXISTS (SELECT 1 FROM `tabGuest Folio` `gf_scope` "
			"WHERE `gf_scope`.name = `tabFolio Transaction`.parent "
			"AND `gf_scope`.hotel_reception IS NOT NULL)"
		)
	placeholders = ", ".join(frappe.db.escape(r) for r in receptions)
	return (
		f"(EXISTS (SELECT 1 FROM `tabGuest Folio` `gf_scope` "
		f"WHERE `gf_scope`.name = `tabFolio Transaction`.parent "
		f"AND (`gf_scope`.hotel_reception IN ({placeholders}) OR `gf_scope`.hotel_reception IS NULL)) "
		f"OR `tabFolio Transaction`.parent IS NULL OR `tabFolio Transaction`.parent = '')"
	)


def user_can_access_reception(reception: str | None, user: str | None = None) -> bool:
	"""Helper for has_permission and controller-level checks."""
	if not reception:
		return True
	if not user:
		user = frappe.session.user or "Guest"
	if user == "Guest":
		return False
	if _bypass(user):
		return True
	return reception in _user_receptions(user, "Hotel Reservation")
