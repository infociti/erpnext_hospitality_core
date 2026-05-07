import sys
import os

# Set CWD to bench root first
bench_path = '/home/erpnext/frappe-bench'
os.chdir(bench_path)

# Add paths
app_path = os.path.join(bench_path, 'apps', 'hospitality_core')
sys.path.append(os.path.join(bench_path, 'apps', 'frappe'))
sys.path.append(app_path)

# Remove script directory from path to avoid shadowing 'hospitality_core' package 
# by the inner directory of the same name
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir in sys.path:
    sys.path.remove(script_dir)

print(f"App Path Added: {app_path}")
print(f"Sys Path: {sys.path}")

try:
    import hospitality_core.hooks
    print("Pre-import of hospitality_core.hooks SUCCESS")
except ImportError as e:
    print(f"Pre-import FAILED: {e}")
    # Force check file existence
    hook_path = os.path.join(app_path, 'hospitality_core', 'hooks.py')
    print(f"Checking if hooks exists at: {hook_path} -> {os.path.exists(hook_path)}")

import frappe
from frappe.utils import add_days, nowdate
import logging

# Patch Logger to avoid file issues
def get_logger(module=None, with_more_info=False, allow_site=True, filter=None, max_size=100_000, file_count=20):
    logger = logging.getLogger(module or "frappe")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(handler)
    return logger

from frappe.utils import logger as frappe_logger_module
frappe.logger = get_logger
frappe_logger_module.get_logger = get_logger
frappe_logger_module.set_log_level = lambda x: None 

def verify_group_booking():
    try:
        frappe.init(site="185.170.58.232", sites_path='sites')
        frappe.connect()
        
        # RELOAD DOCTYPES to ensure JSON changes are reflected in DB/Meta
        print("Reloading DocTypes...")
        frappe.reload_doc("Hospitality Core", "doctype", "Hotel Group Booking Room")
        frappe.reload_doc("Hospitality Core", "doctype", "Hotel Group Booking")
        print("Reload complete.")

        # 1. Setup: Ensure Master Payer Customer exists
        payer = "Test Corp " + frappe.generate_hash(length=5)
        if not frappe.db.exists("Customer", payer):
            c = frappe.new_doc("Customer")
            c.customer_name = payer
            c.customer_type = "Company"
            c.insert(ignore_permissions=True)
            
        # 2. Setup: Ensure Rooms exist
        rooms = ["901", "902", "903"] 
        for r in rooms:
            if not frappe.db.exists("Hotel Room", r):
                room = frappe.new_doc("Hotel Room")
                room.room_number = r
                room.room_type = "Deluxe" 
                room.status = "Available"
                room.save(ignore_permissions=True)
            else:
                # Reset status
                frappe.db.set_value("Hotel Room", r, "status", "Available")
                
        # 3. Create Group Booking
        gb = frappe.new_doc("Hotel Group Booking")
        gb.group_name = "Test Group " + frappe.generate_hash(length=5)
        gb.master_payer = payer
        gb.arrival_date = add_days(nowdate(), 1)
        gb.departure_date = add_days(nowdate(), 3)
        gb.status = "Confirmed"
        
        # Set Master Discount
        gb.discount_type = "Percentage"
        gb.discount_value = 10
        
        # Add Rooms
        # Room 901: Master Payer (Implicitly first)
        gb.append("rooms", {
            "room": "901",
            "room_type": "Deluxe",
            "discount_type": "Percentage",
            "discount_value": 0 # Master discount applies to Master Payer reservation
        })
        
        # Room 902: Child Reservation with specific discount
        gb.append("rooms", {
            "room": "902",
            "room_type": "Deluxe",
            "discount_type": "Amount",
            "discount_value": 50
        })
        
        # Room 903: Child Reservation with NO discount
        gb.append("rooms", {
            "room": "903",
            "room_type": "Deluxe"
        })
        
        print(f"Creating Group Booking: {gb.group_name}")
        gb.insert(ignore_permissions=True)
        
        # Trigger Logic (since it runs on_update if Confirmed)
        # We might need to save again to trigger on_update explicitly if insert doesn't behave identical to save in controller hooks depending on version, 
        # but usually insert calls on_update.
        # However, our logic checks: "if self.status in ['Confirmed']"
        # Since we inserted as Confirmed, it should run.
        
        gb.reload()
        print(f"Master Folio: {gb.master_folio}")
        
        if not gb.master_folio:
            print("FAILURE: Master Folio was not created.")
            return

        # 4. Verify Master Reservation (Room 901)
        master_res = frappe.db.get_value("Hotel Reservation", {
            "group_booking": gb.name,
            "room": "901",
            "status": "Reserved"
        }, ["name", "discount_type", "discount_value", "folio"], as_dict=True)
        
        if master_res:
            print(f"Master Reservation Found: {master_res.name}")
            print(f" - Discount: {master_res.discount_type} {master_res.discount_value}")
            print(f" - Folio: {master_res.folio}")
            
            if master_res.discount_type == "Percentage" and master_res.discount_value == 10:
                print("SUCCESS: Master Discount Correct.")
            else:
                print("FAILURE: Master Discount Incorrect.")
                
            if master_res.folio == gb.master_folio:
                print("SUCCESS: Master Folio Linked Correctly.")
            else:
                print("FAILURE: Master Folio Link Mismatch.")
        else:
            print("FAILURE: Master Reservation (Room 901) NOT FOUND.")

        # 5. Verify Child Reservation (Room 902)
        child_res = frappe.db.get_value("Hotel Reservation", {
            "group_booking": gb.name,
            "room": "902"
        }, ["name", "discount_type", "discount_value"], as_dict=True)
        
        if child_res:
             print(f"Child Reservation (902) Found: {child_res.name}")
             if child_res.discount_type == "Amount" and child_res.discount_value == 50:
                 print("SUCCESS: Child (902) Discount Correct.")
             else:
                 print("FAILURE: Child (902) Discount Incorrect.")
        else:
            print("FAILURE: Child Reservation (902) NOT FOUND.")
            
        # 6. Verify Child Reservation (903)
        child_res_2 = frappe.db.get_value("Hotel Reservation", {
            "group_booking": gb.name,
            "room": "903"
        }, ["name", "discount_type", "discount_value"], as_dict=True)
        
        if child_res_2:
             print(f"Child Reservation (903) Found: {child_res_2.name}")
             if not child_res_2.discount_value:
                  print("SUCCESS: Child (903) has NO discount as expected.")
             else:
                  print("FAILURE: Child (903) should have no discount.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_group_booking()
