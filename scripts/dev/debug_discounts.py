
import frappe

def debug_discounts():
    print("Checking last 5 reservations for discounts...")
    res_list = frappe.db.get_all('Hotel Reservation', 
        order_by='creation desc', 
        limit=5, 
        fields=['name', 'discount_type', 'discount_value', 'is_group_guest', 'group_booking', 'creation']
    )
    
    for r in res_list:
        print(f"Res: {r.name} | Created: {r.creation} | Type: {r.discount_type} | Value: {r.discount_value} | Group: {r.group_booking}")

if __name__ == "__main__":
    debug_discounts()
