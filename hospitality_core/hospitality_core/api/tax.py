"""Reservation tax computation using ERPNext Sales Taxes & Charges Templates.

Supports the common hospitality tax patterns:
- Percentage of net total (most VAT/GST/sales tax)
- Actual amount per row (city tax / occupancy tax expressed as flat fee)
- Cumulative on previous row's net (rare; e.g. service-charge-then-tax)

We deliberately avoid pulling in erpnext.controllers.taxes_and_totals
because that runs over Sales Invoice items — we want a lightweight base
computation we can call from booking quotes and folio postings without
materialising an Invoice.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


def ensure_tax_template_field() -> None:
	"""Idempotent — ensures the `hospitality_tax_template` Custom Field
	exists on Hotel Reception. Wired to after_migrate."""
	if not frappe.db.exists("DocType", "Hotel Reception"):
		return
	if frappe.db.exists(
		"Custom Field", {"dt": "Hotel Reception", "fieldname": "hospitality_tax_template"}
	):
		return
	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": "Hotel Reception",
			"fieldname": "hospitality_tax_template",
			"label": "Tax Template",
			"fieldtype": "Link",
			"options": "Sales Taxes and Charges Template",
			"insert_after": "address_html" if _has_field("Hotel Reception", "address_html") else None,
			"description": "Default tax template for room revenue at this property.",
			"module": "Hospitality Core",
		}
	).insert(ignore_permissions=True)


def _has_field(doctype: str, fieldname: str) -> bool:
	try:
		meta = frappe.get_meta(doctype)
		return any(f.fieldname == fieldname for f in meta.fields)
	except Exception:
		return False


def get_tax_template_for_reception(reception: str) -> str | None:
	"""Per-reception tax template lookup. Stored as a Custom Field on
	Hotel Reception (`hospitality_tax_template`) — created by
	ensure_tax_template_field on after_migrate."""
	if not reception:
		return None
	try:
		tpl = frappe.db.get_value("Hotel Reception", reception, "hospitality_tax_template")
	except Exception:
		return None
	return tpl or None


def compute_tax_breakdown(
	base_amount: float,
	template: str | None,
	*,
	include_zero_rows: bool = False,
) -> dict:
	"""Return a structured tax breakdown for a given base amount.

	Output:
	    {
	      "base_amount": float,
	      "lines": [{"description","rate","amount","account_head","charge_type"}...],
	      "total_tax": float,
	      "grand_total": float,
	      "template": str | None,
	    }

	If template is None or unresolvable, returns {total_tax: 0, grand_total: base}.
	"""
	base = flt(base_amount)
	zero_result = {
		"base_amount": base,
		"lines": [],
		"total_tax": 0.0,
		"grand_total": base,
		"template": template,
	}
	# Allow negative base (refunds reverse the original tax). Skip exact 0
	# because there's nothing to tax. Skip when no template is configured.
	if not template or base == 0:
		return zero_result

	rows = frappe.db.get_all(
		"Sales Taxes and Charges",
		filters={"parent": template, "parenttype": "Sales Taxes and Charges Template"},
		fields=["charge_type", "rate", "tax_amount", "description", "account_head", "row_id"],
		order_by="idx asc",
	)
	if not rows:
		return zero_result

	lines: list[dict] = []
	running_total = base
	prev_amount: float = 0.0
	for r in rows:
		amount = _line_amount(
			r.charge_type,
			flt(r.rate),
			flt(r.tax_amount),
			net_total=base,
			previous_total=running_total,
			previous_row_amount=prev_amount,
		)
		if amount == 0 and not include_zero_rows:
			continue
		lines.append(
			{
				"description": r.description or _("Tax"),
				"rate": flt(r.rate),
				"amount": flt(amount),
				"account_head": r.account_head,
				"charge_type": r.charge_type,
			}
		)
		running_total += amount
		prev_amount = flt(amount)

	total_tax = sum(l["amount"] for l in lines)
	return {
		"base_amount": base,
		"lines": lines,
		"total_tax": flt(total_tax),
		"grand_total": flt(base + total_tax),
		"template": template,
	}


def _line_amount(
	charge_type: str,
	rate: float,
	actual: float,
	*,
	net_total: float,
	previous_total: float,
	previous_row_amount: float,
) -> float:
	"""Compute one tax row's amount. Mirrors the subset of ERPNext's
	tax_and_totals logic that applies to a flat base (no item splits)."""
	ct = (charge_type or "").strip()
	if ct == "Actual":
		return flt(actual)
	if ct == "On Net Total":
		return flt(net_total) * flt(rate) / 100.0
	if ct in ("On Previous Row Amount", "On Previous Row Total"):
		base_for_pct = previous_row_amount if ct == "On Previous Row Amount" else previous_total
		return flt(base_for_pct) * flt(rate) / 100.0
	if ct == "On Item Quantity":
		# Not meaningful here — caller should use Actual instead.
		return 0.0
	return 0.0


# ---- whitelisted helpers -------------------------------------------------


@frappe.whitelist()
def quote_with_tax(
	hotel_reception: str,
	room_type: str,
	arrival_date: str,
	departure_date: str,
	rate_plan: str | None = None,
	guests: int = 1,
) -> dict:
	"""Wraps booking.get_quote and adds a tax breakdown using the
	reception's configured Sales Taxes and Charges Template."""
	from hospitality_core.hospitality_core.api.booking import get_quote

	quote = get_quote(
		hotel_reception=hotel_reception,
		room_type=room_type,
		arrival_date=arrival_date,
		departure_date=departure_date,
		rate_plan=rate_plan,
		guests=guests,
	)
	template = get_tax_template_for_reception(hotel_reception)
	tax = compute_tax_breakdown(quote["subtotal"], template)
	return {
		**quote,
		"tax_template": tax["template"],
		"tax_lines": tax["lines"],
		"tax_total": tax["total_tax"],
		"grand_total": tax["grand_total"],
	}


@frappe.whitelist()
def preview_reservation_taxes(reservation: str) -> dict:
	"""Compute taxes for an existing reservation's expected room revenue
	(rate x nights). Useful for showing the guest a tax-inclusive total
	before posting nightly room charges."""
	res = frappe.db.get_value(
		"Hotel Reservation",
		reservation,
		[
			"hotel_reception",
			"room_type",
			"rate_plan",
			"arrival_date",
			"departure_date",
			"is_complimentary",
			"discount_type",
			"discount_value",
		],
		as_dict=True,
	)
	if not res:
		frappe.throw(_("Reservation {0} not found.").format(reservation))
	if res.is_complimentary:
		return {
			"reservation": reservation,
			"base_amount": 0.0,
			"tax_template": None,
			"tax_lines": [],
			"tax_total": 0.0,
			"grand_total": 0.0,
			"is_complimentary": True,
		}

	from frappe.utils import date_diff

	from hospitality_core.hospitality_core.api.booking import _resolve_base_rate, _resolve_rate_from_plan

	rate = _resolve_rate_from_plan(res.rate_plan, res.room_type, res.arrival_date)
	if not rate:
		rate = _resolve_base_rate(res.room_type, res.arrival_date)
	nights = date_diff(res.departure_date, res.arrival_date)
	base = flt(rate) * nights
	if (res.discount_type or "").strip() == "Percentage" and flt(res.discount_value):
		base = base * (1.0 - flt(res.discount_value) / 100.0)
	elif (res.discount_type or "").strip() == "Amount":
		base = max(0.0, base - flt(res.discount_value))

	template = get_tax_template_for_reception(res.hotel_reception)
	breakdown = compute_tax_breakdown(base, template)
	return {
		"reservation": reservation,
		"is_complimentary": False,
		**breakdown,
	}


def post_tax_for_amount(
	folio: str,
	base_amount: float,
	*,
	posting_date: str | None = None,
	template: str | None = None,
	description: str | None = None,
) -> list[str]:
	"""Append one Folio Transaction per tax line. Returns inserted names.

	Caller is responsible for posting the underlying revenue line first.
	The tax row uses the special item code 'TAX' (auto-created if absent)
	so downstream GL hooks and reports can recognise tax postings.

	Note: this function is **not** idempotent — it always inserts new
	rows. Callers running in a retry loop must either dedupe by their
	own reference or wrap calls in a savepoint they roll back on retry.
	"""
	from frappe.utils import nowdate

	template = template or _resolve_template_from_folio(folio)
	breakdown = compute_tax_breakdown(base_amount, template)
	if not breakdown["lines"]:
		return []

	if not frappe.db.exists("Item", "TAX"):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "TAX",
				"item_name": "Hospitality Tax",
				"item_group": "Services",
				"is_stock_item": 0,
			}
		).insert(ignore_permissions=True)

	posting_date = posting_date or nowdate()
	inserted: list[str] = []
	for line in breakdown["lines"]:
		row = frappe.get_doc(
			{
				"doctype": "Folio Transaction",
				"parent": folio,
				"parenttype": "Guest Folio",
				"parentfield": "transactions",
				"posting_date": posting_date,
				"item": "TAX",
				"description": description or line["description"],
				"qty": 1,
				"amount": flt(line["amount"]),
				"is_void": 0,
			}
		).insert(ignore_permissions=True)
		inserted.append(row.name)
	return inserted


def _resolve_template_from_folio(folio: str) -> str | None:
	"""Best-effort: pull the reception from the folio's reservation, then
	look up the per-reception tax template."""
	reservation = frappe.db.get_value("Guest Folio", folio, "reservation")
	if not reservation:
		return None
	reception = frappe.db.get_value("Hotel Reservation", reservation, "hotel_reception")
	return get_tax_template_for_reception(reception)
