#!/usr/bin/env python3
import frappe
import json
import os

def fix_workspace():
    frappe.init(site='site1.local')
    frappe.connect()
    
    # Delete existing workspace from database
    try:
        if frappe.db.exists('Workspace', 'Hospitality'):
            print("Deleting existing Hospitality workspace from database...")
            frappe.delete_doc('Workspace', 'Hospitality', force=1, ignore_permissions=True)
            frappe.db.commit()
            print("Deleted successfully")
    except Exception as e:
        print(f"Error deleting: {e}")
        frappe.db.rollback()
    
    # Load from JSON file
    json_path = '/home/gifted/frappe-bench/apps/hospitality_core/hospitality_core/hospitality_core/workspace/hospitality/hospitality.json'
    
    if os.path.exists(json_path):
        print(f"Loading workspace from {json_path}...")
        with open(json_path, 'r') as f:
            workspace_data = json.load(f)
        
        # Create new workspace from file
        workspace = frappe.get_doc(workspace_data)
        workspace.insert(ignore_permissions=True)
        frappe.db.commit()
        print("Workspace imported successfully!")
        print(f"Workspace has {len([l for l in workspace.links if l.get('link_type') == 'Report'])} report links")
        
        # Verify configuration
        for link in workspace.links:
            if link.get('label') == 'Daily Sales Consumption':
                print(f"\nDaily Sales Consumption link configuration:")
                print(f"  is_query_report: {link.get('is_query_report')}")
                print(f"  link_to: {link.get('link_to')}")
                print(f"  link_type: {link.get('link_type')}")
                print(f"  report_ref_doctype: {link.get('report_ref_doctype', 'NOT SET')}")
    else:
        print(f"Error: JSON file not found at {json_path}")
    
    frappe.destroy()

if __name__ == '__main__':
    fix_workspace()
