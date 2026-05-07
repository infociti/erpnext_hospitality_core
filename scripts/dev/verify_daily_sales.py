import frappe
from hospitality_core.hospitality_core.report.daily_sales_consumption.daily_sales_consumption import execute

def verify_daily_sales():
    if not frappe.db:
        site = "185.170.58.232"
        frappe.init(site=site, sites_path="../../sites")
        frappe.connect()

    frappe.set_user("Administrator")
    print("Setting up test data...")

    # Ensure TEST-ITEM exists
    if not frappe.db.exists("Item", "TEST-ITEM"):
        item = frappe.new_doc("Item")
        item.item_code = "TEST-ITEM"
        item.item_name = "Test Item"
        item.item_group = "Services"
        item.is_stock_item = 0
        item.insert(ignore_permissions=True)

    # 1. Normal Guest Folio
    folio_normal = frappe.new_doc("Guest Folio")
    folio_normal.status = "Open"
    folio_normal.open_date = frappe.utils.nowdate()
    folio_normal.insert()
    
    frappe.get_doc({
        "doctype": "Folio Transaction",
        "parent": folio_normal.name, "parenttype": "Guest Folio", "parentfield": "transactions",
        "item": "TEST-ITEM", "amount": 100, "description": "Normal Charge", "qty": 1, "is_void": 0
    }).insert()

    # 2. Company Master Folio
    folio_company = frappe.new_doc("Guest Folio")
    folio_company.status = "Open"
    folio_company.is_company_master = 1
    folio_company.open_date = frappe.utils.nowdate()
    # Need a dummy company
    if not frappe.db.exists("Customer", "TEST-COMP"):
        c = frappe.new_doc("Customer")
        c.customer_name = "TEST-COMP"
        c.insert()
    folio_company.company = "TEST-COMP"
    folio_company.insert()
    
    frappe.get_doc({
        "doctype": "Folio Transaction",
        "parent": folio_company.name, "parenttype": "Guest Folio", "parentfield": "transactions",
        "item": "TEST-ITEM", "amount": 200, "description": "Company Charge", "qty": 1, "is_void": 0
    }).insert()

    # 3. Complimentary Reservation
    # Create Guest
    if not frappe.db.exists("Guest", "TEST-GUEST"):
        g = frappe.new_doc("Guest")
        g.full_name = "TEST GUEST"
        g.insert()
    
    # Create Room Type/Room if needed (skipped for simplicity, assuming validation doesn't block)
    # Actually validation might block. Let's try to mock the reservation or just use a folio linked to a mock reservation
    
    res = frappe.new_doc("Hotel Reservation")
    res.guest = "TEST-GUEST"
    res.arrival_date = frappe.utils.nowdate()
    res.departure_date = frappe.utils.add_days(frappe.utils.nowdate(), 1)
    res.is_complimentary = 1
    # Bypass mandatory room/type if possible or create dummy
    if not frappe.db.exists("Hotel Room Type", "TEST-RT"):
        rt = frappe.new_doc("Hotel Room Type")
        rt.room_type_name = "TEST-RT"
        rt.default_rate = 100
        rt.insert()
    res.room_type = "TEST-RT"
        
    if not frappe.db.exists("Hotel Room", "999"):
        rm = frappe.new_doc("Hotel Room")
        rm.room_number = "999"
        rm.room_type = "TEST-RT"
        rm.status = "Vacant"
        rm.insert()
    res.room = "999"
    
    res.insert(ignore_permissions=True)
    
    folio_comp = frappe.get_doc("Guest Folio", res.folio) # Generated automatically
    
    frappe.get_doc({
        "doctype": "Folio Transaction",
        "parent": folio_comp.name, "parenttype": "Guest Folio", "parentfield": "transactions",
        "item": "TEST-ITEM", "amount": 300, "description": "Complimentary Charge", "qty": 1, "is_void": 0
    }).insert()

    try:
        # TEST 1: Default Filter (Exclude Non-Revenue)
        print("Running Report: Default (Exclude)...")
        filters = {
            "from_date": frappe.utils.nowdate(),
            "to_date": frappe.utils.nowdate(),
            "include_non_revenue": 0
        }
        cols, data = execute(filters)
        
        amounts = [d.get("amount") for d in data if d.get("amount")]
        descriptions = [d.get("description") for d in data if d.get("description")]
        
        print(f"Result Descriptions: {descriptions}")
        
        assert "Normal Charge" in descriptions
        assert "Company Charge" not in descriptions
        assert "Complimentary Charge" not in descriptions
        print("✓ Default Filter Passed (Excluded correctly)")

        # TEST 2: Include Non-Revenue
        print("Running Report: Include Non-Revenue...")
        filters["include_non_revenue"] = 1
        cols, data = execute(filters)
        
        descriptions = [d.get("description") for d in data if d.get("description")]
        print(f"Result Descriptions: {descriptions}")
        
        assert "Normal Charge" in descriptions
        assert "Company Charge" in descriptions
        assert "Complimentary Charge" in descriptions
        print("✓ Include Filter Passed")
        
    finally:
        # Cleanup
        frappe.db.delete("Folio Transaction", {"parent": ["in", [folio_normal.name, folio_company.name, folio_comp.name]]})
        frappe.delete_doc("Guest Folio", folio_normal.name)
        frappe.delete_doc("Guest Folio", folio_company.name)
        # Reservation deletion might drive folio deletion
        frappe.delete_doc("Hotel Reservation", res.name)
        # frappe.delete_doc("Guest Folio", folio_comp.name) # might be deleted by reservation
        frappe.db.commit()
        print("Cleanup Done.")

if __name__ == "__main__":
    verify_daily_sales()
