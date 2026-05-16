import frappe
from hospitality_core.hospitality_core.api.folio import sync_folio_balance
from hospitality_core.hospitality_core.report.folio_balance_summary.folio_balance_summary import execute as execute_summary
from hospitality_core.hospitality_core.report.guest_ledger.guest_ledger import execute as execute_guest_ledger

def test_folio_balance():
    frappe.set_user("Administrator")
    
    # 1. Setup - Create a test folio
    folio = frappe.new_doc("Guest Folio")
    folio.status = "Open"
    folio.open_date = frappe.utils.nowdate()
    folio.insert()
    
    folio_name = folio.name
    print(f"Created Test Folio: {folio_name}")
    
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
        
        # 3. Add Overpayment (150) -> Negative amount in Folio Transaction is payment
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
        
        assert folio.outstanding_balance == -50
        assert folio.excess_payment == 50
        print("✓ Folio Logic Verified")
        
        # 6. Verify Summary Report
        cols, data, msg, chart = execute_summary()
        # Find the Guest Ledger row (Private)
        guest_ledger_row = next(r for r in data if r.get("ledger_type") == "Guest Ledger")
        print(f"Summary Report - Guest Ledger Liability: {guest_ledger_row['liability']}")
        assert guest_ledger_row['liability'] >= 50
        print("✓ Summary Report Verified")
        
        # 7. Verify Guest Ledger Report
        cols, data = execute_guest_ledger()
        this_folio_row = next(r for r in data if r.get("name") == folio_name)
        print(f"Guest Ledger - Excess Payment: {this_folio_row['excess_payment']}")
        assert this_folio_row['excess_payment'] == 50
        assert this_folio_row['balance_due'] == 0
        print("✓ Guest Ledger Report Verified")

    finally:
        # Cleanup
        frappe.db.delete("Folio Transaction", {"parent": folio_name})
        frappe.delete_doc("Guest Folio", folio_name)
        frappe.db.commit()
        print("Cleaned up test data.")

if __name__ == "__main__":
    test_folio_balance()
