
import frappe
from hospitality_core.hospitality_core.api.folio import sync_folio_balance

def run_migration():
    print("Starting Folio Balance Migration...")
    # Fetch all open or closed folios that might have transactions
    folios = frappe.get_all("Guest Folio", filters={"status": ["in", ["Open", "Closed"]]}, fields=["name"])
    
    count = 0
    total = len(folios)
    
    for f in folios:
        try:
            doc = frappe.get_doc("Guest Folio", f.name)
            sync_folio_balance(doc)
            count += 1
            if count % 10 == 0:
                print(f"Processed {count}/{total} folios...")
        except Exception as e:
            print(f"Error processing Folio {f.name}: {str(e)}")
            
    frappe.db.commit()
    print(f"Migration complete. {count} folios updated.")

if __name__ == "__main__":
    run_migration()
