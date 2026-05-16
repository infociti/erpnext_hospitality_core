"""Hospitality Core install/setup hooks.

The repository ships both ``setup.py`` (a module) and ``setup/`` (a
package) — Python resolves the package first, which previously caused
``hooks.after_install: hospitality_core.setup.after_install`` to fail
with ``AttributeError``. The original logic from ``setup.py`` is
mirrored here so imports against ``hospitality_core.setup`` resolve
correctly.
"""

import frappe


def after_install():
    create_roles()
    create_custom_fields()
    create_default_data()


def create_roles():
    roles = ["Hospitality User", "Hospitality Manager", "Housekeeping Staff"]
    for role in roles:
        if not frappe.db.exists("Role", role):
            frappe.get_doc(
                {"doctype": "Role", "role_name": role, "desk_access": 1}
            ).insert(ignore_permissions=True)


def create_custom_fields():
    if not frappe.db.exists("Mode of Payment", "Room Charge"):
        mode = frappe.new_doc("Mode of Payment")
        mode.mode_of_payment = "Room Charge"
        mode.type = "General"
        mode.insert(ignore_permissions=True)

    if not frappe.db.exists(
        "Custom Field", {"dt": "POS Invoice", "fieldname": "hotel_room"}
    ):
        frappe.get_doc(
            {
                "doctype": "Custom Field",
                "dt": "POS Invoice",
                "fieldname": "hotel_room",
                "label": "Hotel Room Number",
                "fieldtype": "Link",
                "options": "Hotel Room",
                "insert_after": "customer",
            }
        ).insert(ignore_permissions=True)


def create_default_data():
    reasons = [
        {"code": "POST-ERR", "desc": "Posting Error", "mgr": 0},
        {"code": "GUEST-SAT", "desc": "Guest Satisfaction / Complaint", "mgr": 1},
        {"code": "MGMT-COMP", "desc": "Management Complementary", "mgr": 1},
    ]
    for r in reasons:
        if not frappe.db.exists("Allowance Reason Code", r["code"]):
            frappe.get_doc(
                {
                    "doctype": "Allowance Reason Code",
                    "reason_code": r["code"],
                    "description": r["desc"],
                    "requires_manager_approval": r["mgr"],
                }
            ).insert(ignore_permissions=True)

    items = [
        {"code": "ROOM-RENT", "name": "Room Rent"},
        {"code": "POS-CHARGE", "name": "POS Charge"},
        {"code": "PAYMENT", "name": "Payment Credit"},
        {"code": "TAX", "name": "Hospitality Tax"},
    ]
    for i in items:
        if not frappe.db.exists("Item", i["code"]):
            item = frappe.new_doc("Item")
            item.item_code = i["code"]
            item.item_name = i["name"]
            item.item_group = (
                "Services"
                if frappe.db.exists("Item Group", "Services")
                else "All Item Groups"
            )
            item.is_stock_item = 0
            item.insert(ignore_permissions=True)


def ensure_tax_item():
    """after_migrate hook — guarantees the TAX item exists on existing
    installs that pre-date its addition to create_default_data."""
    if frappe.db.exists("Item", "TAX"):
        return
    item = frappe.new_doc("Item")
    item.item_code = "TAX"
    item.item_name = "Hospitality Tax"
    item.item_group = (
        "Services" if frappe.db.exists("Item Group", "Services") else "All Item Groups"
    )
    item.is_stock_item = 0
    item.insert(ignore_permissions=True)
