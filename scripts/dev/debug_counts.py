
import frappe
frappe.init(site="erpnext.local")
frappe.connect()
print(f"Hotel Room Count: {frappe.db.count('Hotel Room')}")
print(f"Hotel Reservation Count (Today): {frappe.db.count('Hotel Reservation', {'arrival_date': frappe.utils.nowdate()})}")
print(f"Hotel Reservation Statuses (Today): {frappe.db.get_all('Hotel Reservation', filters={'arrival_date': frappe.utils.nowdate()}, fields=['status'])}")
