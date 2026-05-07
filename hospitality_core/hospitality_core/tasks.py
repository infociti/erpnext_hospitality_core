"""Scheduled operational tasks for Hospitality Core.

All tasks are parameterless (per Frappe scheduler contract), run as
Administrator, and commit once per batch. Errors are logged via
frappe.log_error and never crash the scheduler tick.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, add_to_date, flt, get_datetime, getdate, now_datetime, nowdate

from hospitality_core.hospitality_core.utils.audit import log_event


# Default operational windows (could be moved to a Singleton settings doc later)
LOYALTY_EXPIRY_MONTHS = 12
LOST_FOUND_DISPOSE_DAYS = 90
HK_PENDING_BREACH_HOURS = 2
HK_IN_PROGRESS_BREACH_HOURS = 4
LATE_CHECKOUT_REMINDER_HOUR = 12
AUDIT_LOG_RETENTION_DAYS = 365


# ---------- Daily ----------------------------------------------------------

def daily_expire_loyalty_points() -> None:
	"""Expire Earn entries older than LOYALTY_EXPIRY_MONTHS by writing an
	offsetting Expiry entry per guest. Idempotent — only un-expired Earn
	rows that fall outside the window are considered."""
	cutoff = add_to_date(nowdate(), months=-LOYALTY_EXPIRY_MONTHS)
	rows = frappe.db.sql(
		"""
		SELECT guest, SUM(points) AS to_expire
		FROM `tabHospitality Loyalty Entry`
		WHERE entry_type = 'Earn'
		AND DATE(creation) < %(cutoff)s
		AND name NOT IN (
			SELECT IFNULL(le.notes, '')
			FROM `tabHospitality Loyalty Entry` le
			WHERE le.entry_type = 'Expiry'
		)
		AND guest NOT IN (
			SELECT guest FROM `tabHospitality Loyalty Entry`
			WHERE entry_type = 'Expiry'
			AND DATE(creation) >= %(cutoff)s
		)
		GROUP BY guest
		HAVING to_expire > 0
		""",
		{"cutoff": cutoff},
		as_dict=True,
	)

	expired_count = 0
	for row in rows:
		try:
			doc = frappe.get_doc(
				{
					"doctype": "Hospitality Loyalty Entry",
					"guest": row.guest,
					"entry_type": "Expiry",
					"points": flt(row.to_expire),
					"notes": f"Auto-expiry of points earned before {cutoff}",
				}
			)
			doc.insert(ignore_permissions=True)
			expired_count += 1
		except Exception:
			frappe.log_error(title=f"Loyalty expiry failed for {row.guest}")

	if expired_count:
		log_event(
			"loyalty.expiry_run",
			payload={"cutoff": str(cutoff), "guests_affected": expired_count},
		)
	frappe.db.commit()


def daily_dispose_lost_items() -> None:
	"""Auto-dispose Lost and Found items still in 'Found' status after
	LOST_FOUND_DISPOSE_DAYS days. Wraps the existing API helper."""
	from hospitality_core.hospitality_core.api.lost_and_found import (
		auto_dispose_aged_items,
	)

	try:
		result = auto_dispose_aged_items(days=LOST_FOUND_DISPOSE_DAYS)
	except Exception:
		frappe.log_error(title="L&F auto-dispose failed")
		return

	log_event("lost_and_found.auto_dispose", payload=result)
	frappe.db.commit()


def daily_prune_audit_log() -> None:
	"""Hard-delete Hospitality Audit Log entries older than retention.
	Logs a single summary event before deleting."""
	cutoff = add_days(nowdate(), -AUDIT_LOG_RETENTION_DAYS)
	count = frappe.db.count(
		"Hospitality Audit Log",
		filters={"creation": ["<", cutoff]},
	)
	if not count:
		return
	try:
		frappe.db.delete("Hospitality Audit Log", {"creation": ["<", cutoff]})
	except Exception:
		frappe.log_error(title="Audit log prune failed")
		return

	log_event(
		"audit_log.pruned",
		payload={"cutoff": str(cutoff), "rows_deleted": int(count)},
	)
	frappe.db.commit()


# ---------- Hourly ---------------------------------------------------------

def hourly_check_housekeeping_sla() -> None:
	"""Promote breached housekeeping tasks to Urgent priority and emit an
	audit log entry per breach. Runs every hour."""
	now = now_datetime()
	pending_cutoff = add_to_date(now, hours=-HK_PENDING_BREACH_HOURS)
	in_prog_cutoff = add_to_date(now, hours=-HK_IN_PROGRESS_BREACH_HOURS)

	breached = frappe.db.sql(
		"""
		SELECT name, room, status, priority, requested_at, started_at, hotel_reception
		FROM `tabHousekeeping Task`
		WHERE priority != 'Urgent'
		AND (
			(status = 'Pending'  AND requested_at < %(pcut)s)
			OR (status = 'Assigned' AND requested_at < %(pcut)s)
			OR (status = 'In Progress' AND started_at < %(icut)s)
		)
		""",
		{"pcut": pending_cutoff, "icut": in_prog_cutoff},
		as_dict=True,
	)

	for row in breached:
		try:
			frappe.db.set_value(
				"Housekeeping Task",
				row.name,
				{"priority": "Urgent"},
				update_modified=True,
			)
			log_event(
				"housekeeping.sla_breach",
				ref_doctype="Housekeeping Task",
				ref_name=row.name,
				hotel_reception=row.hotel_reception,
				payload={
					"room": row.room,
					"prior_status": row.status,
					"prior_priority": row.priority,
					"requested_at": str(row.requested_at) if row.requested_at else None,
					"started_at": str(row.started_at) if row.started_at else None,
				},
			)
		except Exception:
			frappe.log_error(title=f"HK SLA breach handler failed for {row.name}")

	if breached:
		frappe.db.commit()


def hourly_remind_late_checkouts() -> None:
	"""Notify front-desk users about today's departures still in
	'Checked In' status after the configured cutoff hour. Single realtime
	event per reception per run — front desk console subscribes."""
	if now_datetime().hour < LATE_CHECKOUT_REMINDER_HOUR:
		return

	rows = frappe.db.sql(
		"""
		SELECT name, guest, room, hotel_reception
		FROM `tabHotel Reservation`
		WHERE departure_date = %s
		AND status = 'Checked In'
		""",
		(nowdate(),),
		as_dict=True,
	)
	if not rows:
		return

	by_reception: dict[str, list[dict]] = {}
	for r in rows:
		by_reception.setdefault(r.hotel_reception or "_unknown", []).append(
			{"reservation": r.name, "guest": r.guest, "room": r.room}
		)

	for rec, items in by_reception.items():
		try:
			frappe.publish_realtime(
				event="hospitality_late_checkout_reminder",
				message={"hotel_reception": rec, "pending": items, "count": len(items)},
				room=f"hospitality:{rec}",
			)
			log_event(
				"reservation.late_checkout_reminder",
				hotel_reception=None if rec == "_unknown" else rec,
				payload={"count": len(items), "items": items[:20]},
			)
		except Exception:
			frappe.log_error(title=f"Late checkout reminder failed for {rec}")
