"""Guest Preference APIs.

A guest's preferences are persisted as a single Guest Preference doc keyed
by the Guest's name. Use ``set_preferences`` for upsert; values omitted
from the call are left untouched.
"""

from __future__ import annotations

import frappe
from frappe import _


_EDITABLE_FIELDS = {
	"loyalty_tier",
	"preferred_floor",
	"smoking_preference",
	"bed_type",
	"preferred_temperature",
	"pillow_type",
	"dietary_restrictions",
	"allergies",
	"favorite_drinks",
	"language",
	"marketing_consent_email",
	"marketing_consent_sms",
	"do_not_disturb_window",
	"preferred_check_in_time",
	"special_requests",
	"internal_notes",
}


@frappe.whitelist()
def get_preferences(guest: str) -> dict:
	if not frappe.db.exists("Guest", guest):
		frappe.throw(_("Guest {0} does not exist.").format(guest))
	if not frappe.db.exists("Guest Preference", guest):
		return {"guest": guest, "exists": False}
	doc = frappe.get_doc("Guest Preference", guest)
	doc.check_permission("read")
	return {"exists": True, **{f: doc.get(f) for f in _EDITABLE_FIELDS | {"guest"}}}


@frappe.whitelist()
def set_preferences(guest: str, **kwargs) -> dict:
	if not frappe.db.exists("Guest", guest):
		frappe.throw(_("Guest {0} does not exist.").format(guest))

	if frappe.db.exists("Guest Preference", guest):
		doc = frappe.get_doc("Guest Preference", guest)
		doc.check_permission("write")
	else:
		if not frappe.has_permission("Guest Preference", "create"):
			frappe.throw(_("Not authorised to create guest preferences."))
		doc = frappe.new_doc("Guest Preference")
		doc.guest = guest

	updated = []
	for key, val in (kwargs or {}).items():
		if key not in _EDITABLE_FIELDS:
			continue
		doc.set(key, val)
		updated.append(key)
	doc.save()
	return {"name": doc.name, "updated_fields": updated}


@frappe.whitelist()
def mark_loyalty(guest: str, tier: str) -> dict:
	"""Convenience wrapper for promoting a guest's loyalty tier."""
	allowed = {"Standard", "Silver", "Gold", "Platinum", "VVIP"}
	if tier not in allowed:
		frappe.throw(_("Invalid tier. Must be one of: {0}").format(", ".join(sorted(allowed))))
	return set_preferences(guest, loyalty_tier=tier)


@frappe.whitelist()
def list_vip_guests(reception: str | None = None, limit: int = 100) -> list[dict]:
	"""Return guests with non-Standard loyalty tier — optionally scoped to a reception
	via their most recent reservation."""
	tiers = ("Silver", "Gold", "Platinum", "VVIP")
	if reception:
		rows = frappe.db.sql(
			"""
			SELECT DISTINCT gp.guest, gp.loyalty_tier
			FROM `tabGuest Preference` gp
			INNER JOIN `tabHotel Reservation` res ON res.guest = gp.guest
			WHERE gp.loyalty_tier IN %(tiers)s
			AND res.hotel_reception = %(rec)s
			ORDER BY FIELD(gp.loyalty_tier, 'VVIP','Platinum','Gold','Silver','Standard'), gp.guest
			LIMIT %(limit)s
			""",
			{"tiers": tiers, "rec": reception, "limit": int(limit)},
			as_dict=True,
		)
	else:
		rows = frappe.get_all(
			"Guest Preference",
			fields=["guest", "loyalty_tier"],
			filters={"loyalty_tier": ["in", tiers]},
			order_by="loyalty_tier desc",
			limit=int(limit),
		)
	return rows
