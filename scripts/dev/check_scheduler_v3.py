import sys
import os

# Set CWD to bench root first
bench_path = '/home/erpnext/frappe-bench'
os.chdir(bench_path)

# Add paths
sys.path.append(os.path.join(bench_path, 'apps', 'frappe'))
sys.path.append(os.path.join(bench_path, 'apps', 'hospitality_core'))

import logging
import frappe
import frappe.utils.scheduler
from frappe.utils import logger as frappe_logger_module

# Patch Logger to avoid file issues
def get_logger(module=None, with_more_info=False, allow_site=True, filter=None, max_size=100_000, file_count=20):
    logger = logging.getLogger(module or "frappe")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    logger.addHandler(handler)
    return logger

frappe.logger = get_logger
frappe_logger_module.get_logger = get_logger
frappe_logger_module.set_log_level = lambda x: None

def check_status():
    print(f"CWD: {os.getcwd()}")
    try:
        frappe.init(site="185.170.58.232", sites_path='sites')
        frappe.connect()

        # Check if scheduler is enabled
        scheduler_status = not frappe.utils.scheduler.is_scheduler_disabled()
        print(f"Scheduler Enabled: {scheduler_status}")

        if not scheduler_status:
           print("Attempting to ENABLE scheduler...")
           frappe.utils.scheduler.enable_scheduler()
           frappe.db.commit()
           print("Scheduler Enabled via Script.")

        # Check the specific job (removed next_execution which caused error)
        job_name = "hospitality_core.hospitality_core.api.night_audit.run_daily_audit" 
        job = frappe.db.get_value("Scheduled Job Type", {"method": job_name}, ["name", "stopped", "last_execution", "frequency"], as_dict=True)
        
        if job:
            print(f"Job Found: {job.name}")
            print(f"Stopped: {job.stopped}")
            print(f"Frequency: {job.frequency}")
            print(f"Last Execution: {job.last_execution}")
        else:
            print(f"Job NOT Found: {job_name}")
            # Try to list all jobs to see if there's a typo
            print("Available Jobs in DB (all):")
            all_jobs = frappe.get_all("Scheduled Job Type", fields=["method"])
            for j in all_jobs:
               print(f" - {j.method}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_status()
