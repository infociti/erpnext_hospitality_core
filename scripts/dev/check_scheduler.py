import frappe
from frappe.utils import get_site_path

def check_status():
    try:
        # Check if scheduler is enabled
        scheduler_status = frappe.utils.scheduler.is_scheduler_enabled()
        print(f"Scheduler Enabled: {scheduler_status}")

        # Check the specific job
        job_name = "hospitality_core.hospitality_core.api.night_audit.run_daily_audit"
        job = frappe.db.get_value("Scheduled Job Type", {"method": job_name}, ["name", "stopped", "last_execution", "next_execution"], as_dict=True)
        
        if job:
            print(f"Job Found: {job.name}")
            print(f"Stopped: {job.stopped}")
            print(f"Last Execution: {job.last_execution}")
            print(f"Next Execution: {job.next_execution}")
        else:
            print(f"Job NOT Found: {job_name}")
            # Try to list all jobs to see if there's a typo
            all_jobs = frappe.get_all("Scheduled Job Type", fields=["method"])
            print("Available Jobs:")
            for j in all_jobs:
                if "hospitality" in j.method:
                    print(f" - {j.method}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    frappe.init(site="185.170.58.232")
    frappe.connect()
    check_status()
