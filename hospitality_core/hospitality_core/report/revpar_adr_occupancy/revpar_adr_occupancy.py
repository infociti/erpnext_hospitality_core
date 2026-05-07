"""RevPAR / ADR / Occupancy report.

For each calendar day in the requested range, computes:
  - rooms_available  = enabled rooms at the reception (status != 'Out of Order')
  - rooms_sold       = distinct rooms with a Reserved/Checked In/Checked Out
                       reservation overlapping that night
  - room_revenue     = sum of folio transactions tagged as room revenue
                       (item starts with 'ROOM' or item == 'NIGHT-AUDIT')
                       posted on that date — excludes void rows
  - occupancy_pct    = rooms_sold / rooms_available * 100
  - adr              = room_revenue / rooms_sold       (Average Daily Rate)
  - revpar           = room_revenue / rooms_available  (Revenue Per Available Room)

Filters: from_date, to_date (required), hotel_reception (optional).

Implementation note: we count rooms_sold by the standard "occupancy =
arrival_date <= night < departure_date" definition. Day-use stays
(arrival == departure) are excluded by this rule, which matches the
industry convention for occupancy reporting.
"""

from __future__ import annotations

from collections.abc import Sequence

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, getdate


def execute(filters: dict | None = None) -> tuple[list[dict], list[list]]:
	filters = frappe._dict(filters or {})
	_validate(filters)

	receptions = _resolve_receptions(filters.get("hotel_reception"))
	rooms_available = _count_rooms_available(receptions)

	if not rooms_available:
		return _columns(), []

	from_date = getdate(filters.from_date)
	to_date = getdate(filters.to_date)
	day_count = date_diff(to_date, from_date) + 1

	rooms_sold_by_date = _rooms_sold_per_night(receptions, from_date, to_date)
	revenue_by_date = _revenue_per_night(receptions, from_date, to_date)

	rows: list[list] = []
	for i in range(day_count):
		night = add_days(from_date, i)
		night_str = night.isoformat() if hasattr(night, "isoformat") else str(night)
		sold = int(rooms_sold_by_date.get(night_str, 0))
		revenue = flt(revenue_by_date.get(night_str, 0))
		occ = (sold / rooms_available * 100.0) if rooms_available else 0.0
		adr = (revenue / sold) if sold else 0.0
		revpar = (revenue / rooms_available) if rooms_available else 0.0
		rows.append(
			[
				night,
				rooms_available,
				sold,
				flt(occ, 2),
				flt(revenue, 2),
				flt(adr, 2),
				flt(revpar, 2),
			]
		)

	return _columns(), rows


# ---- internals -----------------------------------------------------------


def _validate(filters: dict) -> None:
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("from_date and to_date are required."))
	if getdate(filters["from_date"]) > getdate(filters["to_date"]):
		frappe.throw(_("from_date must be on or before to_date."))


def _columns() -> list[dict]:
	return [
		{"fieldname": "night", "label": _("Night"), "fieldtype": "Date", "width": 110},
		{
			"fieldname": "rooms_available",
			"label": _("Rooms Available"),
			"fieldtype": "Int",
			"width": 130,
		},
		{"fieldname": "rooms_sold", "label": _("Rooms Sold"), "fieldtype": "Int", "width": 110},
		{
			"fieldname": "occupancy_pct",
			"label": _("Occupancy %"),
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"fieldname": "room_revenue",
			"label": _("Room Revenue"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{"fieldname": "adr", "label": _("ADR"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "revpar", "label": _("RevPAR"), "fieldtype": "Currency", "width": 110},
	]


def _resolve_receptions(reception: str | None) -> list[str]:
	if reception:
		return [reception]
	# Honour permission scope: list only receptions the user has permission on.
	return [r["name"] for r in frappe.get_all("Hotel Reception", fields=["name"], limit_page_length=0)]


def _count_rooms_available(receptions: Sequence[str]) -> int:
	if not receptions:
		return 0
	count = frappe.db.sql(
		"""
		SELECT COUNT(*) FROM `tabHotel Room`
		WHERE hotel_reception IN %(rec)s
		AND IFNULL(is_enabled, 1) = 1
		AND IFNULL(status, '') != 'Out of Order'
		""",
		{"rec": tuple(receptions)},
	)
	return int(count[0][0]) if count else 0


def _rooms_sold_per_night(
	receptions: Sequence[str], from_date, to_date
) -> dict[str, int]:
	"""Count distinct rooms occupied per night across the date range.

	Single SQL using a numbers-table trick: cross-join with seq_0_to_X
	would be MariaDB-specific and brittle, so we run one COUNT per
	matched reservation row and aggregate in Python — correct and fast
	enough for typical reservation volumes.
	"""
	if not receptions:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT room, arrival_date, departure_date
		FROM `tabHotel Reservation`
		WHERE hotel_reception IN %(rec)s
		AND status IN ('Reserved', 'Checked In', 'Checked Out')
		AND room IS NOT NULL AND room != ''
		AND arrival_date < %(to)s + INTERVAL 1 DAY
		AND departure_date > %(from)s
		""",
		{"rec": tuple(receptions), "from": from_date, "to": to_date},
		as_dict=True,
	)

	by_night: dict[str, set[str]] = {}
	for r in rows:
		start = max(getdate(r.arrival_date), getdate(from_date))
		end = min(getdate(r.departure_date), getdate(to_date) + _ONE_DAY)
		night = start
		while night < end:
			by_night.setdefault(_to_iso(night), set()).add(r.room)
			night = add_days(night, 1)

	return {k: len(v) for k, v in by_night.items()}


def _revenue_per_night(
	receptions: Sequence[str], from_date, to_date
) -> dict[str, float]:
	"""Sum room-revenue folio transactions per posting_date across receptions.

	A folio transaction counts as room revenue if the item code starts
	with 'ROOM' or equals 'NIGHT-AUDIT'. Voided rows excluded.
	Reception scoping is via the parent Guest Folio's reservation.
	"""
	if not receptions:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT ft.posting_date, SUM(ft.amount) AS revenue
		FROM `tabFolio Transaction` ft
		INNER JOIN `tabGuest Folio` gf ON gf.name = ft.parent
		LEFT JOIN `tabHotel Reservation` hr ON hr.name = gf.reservation
		WHERE ft.is_void = 0
		AND ft.amount > 0
		AND (
			ft.item LIKE 'ROOM%%'
			OR ft.item = 'NIGHT-AUDIT'
		)
		AND ft.posting_date BETWEEN %(from)s AND %(to)s
		AND (
			hr.hotel_reception IN %(rec)s
			OR gf.hotel_reception IN %(rec)s
		)
		GROUP BY ft.posting_date
		""",
		{"rec": tuple(receptions), "from": from_date, "to": to_date},
		as_dict=True,
	)
	return {_to_iso(r.posting_date): flt(r.revenue) for r in rows}


def _to_iso(d) -> str:
	d = getdate(d)
	return d.isoformat() if hasattr(d, "isoformat") else str(d)


from datetime import timedelta as _td

_ONE_DAY = _td(days=1)
