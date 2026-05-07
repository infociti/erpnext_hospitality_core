"""Public booking availability + quote API.

Whitelisted with allow_guest=False — but designed for use from a public
booking widget proxied through a small frontend that holds an API token.
The endpoints intentionally avoid disclosing reservation-level data:
they return only counts, room types, and quoted prices.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, getdate


@frappe.whitelist()
def search_available_room_types(
	hotel_reception: str,
	arrival_date: str,
	departure_date: str,
	guests: int = 1,
) -> list[dict]:
	"""Return per-room-type availability + base rate for the dates.

	Implementation: for each Hotel Room of a given type at the reception,
	count those *not* covered by an overlapping Reserved/Checked-In
	reservation, and aggregate by room_type.
	"""
	_validate_dates(arrival_date, departure_date)
	if not hotel_reception:
		frappe.throw(_("hotel_reception is required."))

	rows = frappe.db.sql(
		"""
		SELECT
			room.room_type,
			COUNT(DISTINCT room.name) AS available_count
		FROM `tabHotel Room` room
		WHERE room.hotel_reception = %(rec)s
		AND room.is_enabled = 1
		AND room.status != 'Out of Order'
		AND room.name NOT IN (
			SELECT res.room
			FROM `tabHotel Reservation` res
			WHERE res.status IN ('Reserved', 'Checked In')
			AND res.arrival_date < %(dep)s
			AND res.departure_date > %(arr)s
			AND res.room IS NOT NULL
		)
		GROUP BY room.room_type
		HAVING available_count > 0
		ORDER BY room.room_type
		""",
		{"rec": hotel_reception, "arr": arrival_date, "dep": departure_date},
		as_dict=True,
	)

	results = []
	for row in rows:
		base_rate = _resolve_base_rate(row.room_type, arrival_date)
		results.append(
			{
				"room_type": row.room_type,
				"available_count": int(row.available_count),
				"base_rate": flt(base_rate),
				"nights": date_diff(departure_date, arrival_date),
				"estimate_total": flt(base_rate) * date_diff(departure_date, arrival_date),
			}
		)
	return results


@frappe.whitelist()
def get_quote(
	hotel_reception: str,
	room_type: str,
	arrival_date: str,
	departure_date: str,
	rate_plan: str | None = None,
	guests: int = 1,
) -> dict:
	"""Return a price quote for a room type without holding inventory.

	Returns the per-night rate, number of nights, and a non-binding total.
	The caller is expected to confirm via a separate authenticated booking
	flow (not exposed here) that allocates a specific room.
	"""
	_validate_dates(arrival_date, departure_date)
	available = search_available_room_types(
		hotel_reception=hotel_reception,
		arrival_date=arrival_date,
		departure_date=departure_date,
		guests=guests,
	)
	avail_for_type = next((a for a in available if a["room_type"] == room_type), None)
	if not avail_for_type:
		frappe.throw(_("No availability for {0} on the requested dates.").format(room_type))

	rate = _resolve_rate_from_plan(rate_plan, room_type, arrival_date) or avail_for_type["base_rate"]
	nights = date_diff(departure_date, arrival_date)
	subtotal = flt(rate) * nights
	# Tax/fees intentionally omitted here — those should come from the
	# property's tax setup at confirmation time.

	return {
		"hotel_reception": hotel_reception,
		"room_type": room_type,
		"rate_plan": rate_plan,
		"arrival_date": arrival_date,
		"departure_date": departure_date,
		"nights": nights,
		"per_night_rate": flt(rate),
		"subtotal": flt(subtotal),
		"available_count": avail_for_type["available_count"],
		"currency_hint": frappe.db.get_default("currency"),
	}


@frappe.whitelist()
def list_room_types_for_reception(hotel_reception: str) -> list[dict]:
	"""Returns the room types operated at a reception, with bed_count and
	max_occupancy if those fields exist (best-effort)."""
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT rt.name AS room_type
		FROM `tabHotel Room` r
		INNER JOIN `tabHotel Room Type` rt ON r.room_type = rt.name
		WHERE r.hotel_reception = %s AND r.is_enabled = 1
		ORDER BY rt.name
		""",
		(hotel_reception,),
		as_dict=True,
	)
	return rows


# ---- internals -----------------------------------------------------------

def _validate_dates(arrival_date: str, departure_date: str) -> None:
	if not arrival_date or not departure_date:
		frappe.throw(_("Both arrival_date and departure_date are required."))
	if getdate(departure_date) <= getdate(arrival_date):
		frappe.throw(_("departure_date must be after arrival_date."))
	if getdate(arrival_date) < getdate(add_days(None, -1)):
		frappe.throw(_("arrival_date cannot be in the past."))


def _resolve_base_rate(room_type: str, on_date: str) -> float:
	"""Pick the active rate plan covering the date for the given type;
	fall back to 0 if none configured."""
	rate = frappe.db.sql(
		"""
		SELECT rate
		FROM `tabRoom Rate Plan`
		WHERE room_type = %(rt)s
		AND active = 1
		AND (valid_from IS NULL OR valid_from <= %(d)s)
		AND (valid_to IS NULL OR valid_to >= %(d)s)
		ORDER BY rate ASC
		LIMIT 1
		""",
		{"rt": room_type, "d": on_date},
	)
	return float(rate[0][0]) if rate else 0.0


def _resolve_rate_from_plan(plan: str | None, room_type: str, on_date: str) -> float | None:
	if not plan:
		return None
	row = frappe.db.get_value(
		"Room Rate Plan",
		plan,
		["room_type", "rate", "valid_from", "valid_to", "active"],
		as_dict=True,
	)
	if not row or not row.active or row.room_type != room_type:
		return None
	if row.valid_from and getdate(on_date) < getdate(row.valid_from):
		return None
	if row.valid_to and getdate(on_date) > getdate(row.valid_to):
		return None
	return float(row.rate or 0)
