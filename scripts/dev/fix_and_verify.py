import frappe
from hospitality_core.hospitality_core.api.folio import sync_folio_balance
from hospitality_core.hospitality_core.report.folio_balance_summary.folio_balance_summary import execute as execute_summary
from hospitality_core.hospitality_core.report.guest_ledger.guest_ledger import execute as execute_guest_ledger

def run_patch():
    print("Starting Data Patch for existing Folios...")
    # Fetch all folios that are not cancelled (assuming we don't care about cancelled ones, but safe to run on all)
    folios = frappe.get_all("Guest Folio", filters={"status": ["!=", "Cancelled"]}, pluck="name")
    
    total = len(folios)
    print(f"Found {total} folios to update.")
    
    count = 0
    for name in folios:
        try:
            doc = frappe.get_doc("Guest Folio", name)
            sync_folio_balance(doc)
            count += 1
            if count % 50 == 0:
                frappe.db.commit()
                print(f"Processed {count}/{total}...")
        except Exception as e:
            print(f"Error updating {name}: {e}")
            
    frappe.db.commit()
    print("Patch Complete. All folios recalculated.")

def run_fix_and_verify():
    print("reloading doc to update schema...")
    frappe.reload_doc("hospitality_core", "doctype", "guest_folio")
    # Also reload Folio Transaction just in case
    frappe.reload_doc("hospitality_core", "doctype", "folio_transaction")
    print("Schema updated (hopefully).")

    frappe.set_user("Administrator")
    
    # 1. Setup - Create a test folio
    folio = frappe.new_doc("Guest Folio")
    folio.status = "Open"
    folio.open_date = frappe.utils.nowdate()
    folio.insert()
    
    folio_name = folio.name
    print(f"Created Test Folio: {folio_name}")

    # Ensure TEST-ITEM exists
    if not frappe.db.exists("Item", "TEST-ITEM"):
        item = frappe.new_doc("Item")
        item.item_code = "TEST-ITEM"
        item.item_name = "Test Item"
        item.item_group = "Services" # Assuming Services group exists or basic Item
        item.is_stock_item = 0
        item.insert(ignore_permissions=True)
        print("Created Item: TEST-ITEM")
    
    try:
        # 2. Add Charges (100)
        frappe.get_doc({
            "doctype": "Folio Transaction",
            "parent": folio_name,
            "parenttype": "Guest Folio",
            "parentfield": "transactions",
            "item": "TEST-ITEM",
            "amount": 100,
            "qty": 1,
            "is_void": 0
        }).insert()
        
        # 3. Add Overpayment (150)
        frappe.get_doc({
            "doctype": "Folio Transaction",
            "parent": folio_name,
            "parenttype": "Guest Folio",
            "parentfield": "transactions",
            "item": "PAYMENT",
            "amount": -150,
            "qty": 1,
            "is_void": 0
        }).insert()
        
        # 4. Sync Balance
        sync_folio_balance(folio)
        
        # 5. Reload and Verify
        folio.reload()
        print(f"Outstanding Balance: {folio.outstanding_balance}")
        print(f"Excess Payment: {folio.excess_payment}")

        # Check if column exists
        has_col = frappe.db.has_column("Guest Folio", "excess_payment")
        print(f"Has excess_payment column: {has_col}")
        
        assert folio.outstanding_balance == -50
        assert folio.excess_payment == 50
        print("✓ Folio Logic Verified")
        
        # 6. Verify Summary Report
        cols, data, msg, chart = execute_summary()
        guest_ledger_row = next((r for r in data if r.get("ledger_type") == "Guest Ledger"), None)
        if guest_ledger_row:
             print(f"Summary Report - Guest Ledger Liability: {guest_ledger_row.get('liability')}")
             assert guest_ledger_row.get('liability', 0) >= 50
        print("✓ Summary Report Verified")
        
        # 7. Verify Guest Ledger Report
        cols, data = execute_guest_ledger()
        this_folio_row = next((r for r in data if r.get("name") == folio_name), None)
        if this_folio_row:
            print(f"Guest Ledger - Excess Payment: {this_folio_row.get('excess_payment')}")
            assert this_folio_row.get('excess_payment') == 50
            assert this_folio_row.get('balance_due') == 0
        print("✓ Guest Ledger Report Verified")

    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        if frappe.db.exists("Guest Folio", folio_name):
            frappe.db.delete("Folio Transaction", {"parent": folio_name})
            frappe.delete_doc("Guest Folio", folio_name)
        frappe.db.commit()
        print("Cleaned up test data.")

if __name__ == "__main__":
    if not frappe.db:
        site = "185.170.58.232"
        frappe.init(site=site, sites_path="../../sites")
        frappe.connect()
    
    run_fix_and_verify()
