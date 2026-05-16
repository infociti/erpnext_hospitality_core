"""Multi-tenant permission scoping for Hospitality Core.

Each property is identified by Hotel Reception. A user is granted access to
a reception by adding a User Permission (allow=Hotel Reception, for_value=<reception>).

System Manager / Hospitality Manager / Administrator bypass scoping.

Failure mode: a user with **no** Hotel Reception permissions sees **no rows**
(``1=0``). We do not surface ``hotel_reception IS NULL`` rows because that
would unintentionally leak any orphan record (e.g. a misconfigured audit
log entry). Operators should explicitly grant a reception permission to
each non-bypass user.
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
		return "1=0"
	placeholders = ", ".join(frappe.db.escape(r) for r in receptions)
	return f"{alias}.hotel_reception IN ({placeholders})"


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


def concierge_request_query(user: str | None = None) -> str:
	return _scope_clause(user, "Concierge Request", "`tabConcierge Request`")


def minibar_consumption_query(user: str | None = None) -> str:
	return _scope_clause(user, "Minibar Consumption", "`tabMinibar Consumption`")


def loyalty_entry_query(user: str | None = None) -> str:
	"""Loyalty entries are scoped via the linked Hotel Reservation's reception."""
	if not user:
		user = frappe.session.user or "Guest"
	if user == "Guest":
		return "1=0"
	if _bypass(user):
		return ""
	receptions = _user_receptions(user, "Hotel Reservation")
	if not receptions:
		return "1=0"
	placeholders = ", ".join(frappe.db.escape(r) for r in receptions)
	return (
		f"EXISTS (SELECT 1 FROM `tabHotel Reservation` `res_scope` "
		f"WHERE `res_scope`.name = `tabHospitality Loyalty Entry`.reservation "
		f"AND `res_scope`.hotel_reception IN ({placeholders}))"
	)


def hospitality_audit_log_query(user: str | None = None) -> str:
	return _scope_clause(user, "Hospitality Audit Log", "`tabHospitality Audit Log`")


def hotel_maintenance_request_query(user: str | None = None) -> str:
	"""Maintenance requests reference a Hotel Room — scope via that room's reception."""
	if not user:
		user = frappe.session.user or "Guest"
	if user == "Guest":
		return "1=0"
	if _bypass(user):
		return ""
	receptions = _user_receptions(user, "Hotel Room")
	if not receptions:
		return "1=0"
	placeholders = ", ".join(frappe.db.escape(r) for r in receptions)
	return (
		f"EXISTS (SELECT 1 FROM `tabHotel Room` `rm_scope` "
		f"WHERE `rm_scope`.name = `tabHotel Maintenance Request`.room "
		f"AND `rm_scope`.hotel_reception IN ({placeholders}))"
	)


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
		return "1=0"
	placeholders = ", ".join(frappe.db.escape(r) for r in receptions)
	return (
		f"EXISTS (SELECT 1 FROM `tabGuest Folio` `gf_scope` "
		f"WHERE `gf_scope`.name = `tabFolio Transaction`.parent "
		f"AND `gf_scope`.hotel_reception IN ({placeholders}))"
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
