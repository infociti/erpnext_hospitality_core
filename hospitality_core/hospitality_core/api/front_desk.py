"""Front-desk operations: batch check-in and walk list.

`batch_check_in` is a flexible group-of-reservations check-in that does
*not* require a Hotel Group Booking — useful for tour buses, conference
arrivals, or any clustered front-desk activity.

`walk_list` returns the front-desk daily snapshot (arrivals, in-house,
departures, late-checkouts, expected-arrivals-not-yet-arrived) suited
for a single-page operational dashboard.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


@frappe.whitelist(methods=["POST"])
def batch_check_in(reservations: str | list[str], stop_on_error: int = 0) -> dict:
	"""Check-in multiple reservations in one call.

	Accepts either a list or a JSON-encoded list of reservation names.
	Each entry is processed in its own SAVEPOINT so a single failure
	rolls back only that row — earlier successes survive. Set
	`stop_on_error` to halt the batch on the first failure.
	"""
	return _run_batch(reservations, "process_check_in", "Checked In", int(stop_on_error or 0))


@frappe.whitelist(methods=["POST"])
def batch_check_out(reservations: str | list[str], stop_on_error: int = 0) -> dict:
	"""Symmetric counterpart to batch_check_in."""
	return _run_batch(reservations, "process_check_out", "Checked Out", int(stop_on_error or 0))


def _run_batch(reservations, method_name: str, ok_status: str, stop_on_error: int) -> dict:
	names = _coerce_list(reservations)
	if not names:
		frappe.throw(_("No reservations supplied."))

	results: list[dict] = []
	succeeded = 0
	for idx, name in enumerate(names):
		# Per-row savepoint: rolls back this row's writes only,
		# preserving successful peers committed in the same transaction.
		sp = f"hc_batch_{idx}"
		frappe.db.savepoint(sp)
		try:
			doc = frappe.get_doc("Hotel Reservation", name)
			getattr(doc, method_name)()
			results.append({"reservation": name, "status": ok_status, "ok": True})
			succeeded += 1
		except Exception as exc:
			frappe.db.rollback(save_point=sp)
			results.append({"reservation": name, "ok": False, "error": str(exc)})
			if stop_on_error:
				break

	return {
		"succeeded": succeeded,
		"failed": len(results) - succeeded,
		"total": len(results),
		"results": results,
	}


@frappe.whitelist(methods=["GET"])
def walk_list(hotel_reception: str | None = None, on_date: str | None = None) -> dict:
	"""Front-desk daily walk list — five buckets in one round-trip.

	Buckets:
	- expected_arrivals  — Reserved with arrival_date == on_date
	- arrived            — already Checked In with arrival_date == on_date
	- in_house           — Checked In, mid-stay
	- expected_departures — Checked In with departure_date == on_date
	- late_checkouts     — Checked In with departure_date < on_date

	Each bucket carries the reservation summary + guest_name + room +
	folio outstanding balance for at-a-glance decisioning.
	"""
	on_date = on_date or nowdate()

	rows = frappe.db.sql(
		"""
		SELECT
			res.name,
			res.guest,
			res.hotel_reception,
			res.room,
			res.room_type,
			res.arrival_date,
			res.departure_date,
			res.status,
			res.folio,
			res.is_complimentary,
			res.is_company_guest,
			res.is_group_guest,
			res.group_booking,
			gst.full_name AS guest_name,
			gst.mobile_no,
			gf.outstanding_balance
		FROM `tabHotel Reservation` res
		LEFT JOIN `tabGuest` gst ON gst.name = res.guest
		LEFT JOIN `tabGuest Folio` gf ON gf.name = res.folio
		WHERE res.status IN ('Reserved', 'Checked In')
		AND (
			res.arrival_date = %(d)s
			OR res.departure_date = %(d)s
			OR (res.arrival_date < %(d)s AND res.departure_date >= %(d)s AND res.status = 'Checked In')
			OR (res.departure_date < %(d)s AND res.status = 'Checked In')
		)
		{rec_clause}
		ORDER BY res.arrival_date ASC, res.room ASC
		""".format(rec_clause=("AND res.hotel_reception = %(rec)s" if hotel_reception else "")),
		{"d": on_date, "rec": hotel_reception},
		as_dict=True,
	)

	buckets = {
		"expected_arrivals": [],
		"arrived": [],
		"in_house": [],
		"expected_departures": [],
		"late_checkouts": [],
	}
	on = getdate(on_date)
	for r in rows:
		arr = getdate(r.arrival_date) if r.arrival_date else None
		dep = getdate(r.departure_date) if r.departure_date else None
		summary = _summarise(r)

		if r.status == "Reserved" and arr == on:
			buckets["expected_arrivals"].append(summary)
		elif r.status == "Checked In" and arr == on:
			buckets["arrived"].append(summary)
		elif r.status == "Checked In" and dep == on:
			buckets["expected_departures"].append(summary)
		elif r.status == "Checked In" and dep and dep < on:
			buckets["late_checkouts"].append(summary)
		elif r.status == "Checked In":
			buckets["in_house"].append(summary)

	return {
		"on_date": str(on_date),
		"hotel_reception": hotel_reception,
		"counts": {k: len(v) for k, v in buckets.items()},
		**buckets,
	}


@frappe.whitelist(methods=["GET"])
def list_group_arrivals_today(hotel_reception: str | None = None) -> list[dict]:
	"""Returns expected-arrival reservations grouped by Hotel Group Booking
	for today, so the front desk can prep registration packets per group."""
	on_date = nowdate()
	rows = frappe.db.sql(
		"""
		SELECT
			gb.name AS group_booking,
			gb.group_name,
			COUNT(res.name) AS pax_count,
			SUM(CASE WHEN res.status = 'Reserved' THEN 1 ELSE 0 END) AS pending,
			SUM(CASE WHEN res.status = 'Checked In' THEN 1 ELSE 0 END) AS arrived
		FROM `tabHotel Group Booking` gb
		INNER JOIN `tabHotel Reservation` res ON res.group_booking = gb.name
		WHERE res.arrival_date = %(d)s
		{rec_clause}
		GROUP BY gb.name, gb.group_name
		ORDER BY gb.group_name
		""".format(rec_clause=("AND res.hotel_reception = %(rec)s" if hotel_reception else "")),
		{"d": on_date, "rec": hotel_reception},
		as_dict=True,
	)
	return rows


# ---- internals -----------------------------------------------------------


def _coerce_list(value: str | list[str]) -> list[str]:
	if isinstance(value, str):
		try:
			parsed = json.loads(value)
		except (ValueError, TypeError):
			# Treat as comma-separated string
			parsed = [v.strip() for v in value.split(",") if v.strip()]
		if isinstance(parsed, list):
			return [str(v).strip() for v in parsed if str(v).strip()]
		if isinstance(parsed, str):
			return [parsed]
		return []
	if isinstance(value, list):
		return [str(v).strip() for v in value if str(v).strip()]
	return []


def _summarise(r: dict) -> dict:
	return {
		"reservation": r.name,
		"guest": r.guest,
		"guest_name": r.guest_name,
		"mobile_no": r.mobile_no,
		"hotel_reception": r.hotel_reception,
		"room": r.room,
		"room_type": r.room_type,
		"arrival_date": str(r.arrival_date) if r.arrival_date else None,
		"departure_date": str(r.departure_date) if r.departure_date else None,
		"status": r.status,
		"folio": r.folio,
		"outstanding_balance": flt(r.outstanding_balance),
		"is_complimentary": bool(r.is_complimentary),
		"is_company_guest": bool(r.is_company_guest),
		"is_group_guest": bool(r.is_group_guest),
		"group_booking": r.group_booking,
	}
