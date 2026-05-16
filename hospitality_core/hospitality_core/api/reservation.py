import frappe
from frappe import _
from frappe.utils import add_days, date_diff, getdate, nowdate

def check_availability(room, arrival_date, departure_date, ignore_reservation=None):
    """
    Checks if a room is available for the given date range.
    Returns True if available, raises ValidationError if not.
    """
    if not room or not arrival_date or not departure_date:
        return

    # 1. Check if Room is Enabled (Maintenance check)
    room_status = frappe.db.get_value("Hotel Room", room, ["status", "is_enabled"], as_dict=True)
    if not room_status.is_enabled:
        frappe.throw(_("Room {0} is currently disabled/under maintenance.").format(room))
    
    if room_status.status == "Out of Order":
        frappe.throw(_("Room {0} is marked Out of Order.").format(room))

    # 2. Check Overlapping Reservations
    # Logic: New Arrival < Existing Departure AND New Departure > Existing Arrival
    filters = {
        "room": room,
        "status": ["in", ["Reserved", "Checked In"]],
        "name": ["!=", ignore_reservation] if ignore_reservation else ["is", "set"]
    }
    
    existing_bookings = frappe.get_all("Hotel Reservation", 
        filters=filters,
        fields=["name", "arrival_date", "departure_date", "guest"]
    )

    for booking in existing_bookings:
        # Check for date overlap
        if (getdate(arrival_date) < getdate(booking.departure_date)) and \
           (getdate(departure_date) > getdate(booking.arrival_date)):
             frappe.throw(
                _("Room {0} is already booked by {1} from {2} to {3} (Reservation: {4})").format(
                    room, booking.guest, booking.arrival_date, booking.departure_date, booking.name
                )
             )
    
    return True

def check_bulk_availability(rooms, arrival_date, departure_date, ignore_reservation=None):
    """
    Checks if a list of rooms is available for the given date range.
    Collects ALL conflicts and raises a single ValidationError.
    """
    if not rooms or not arrival_date or not departure_date:
        return

    conflicts = []

    # 1. Check Room Maintenance Status for all rooms in batch
    room_data = frappe.get_all("Hotel Room", 
        filters={"room_number": ["in", rooms]},
        fields=["room_number", "status", "is_enabled"]
    )
    
    room_map = {r.room_number: r for r in room_data}

    for room_num in rooms:
        r = room_map.get(room_num)
        if not r:
            conflicts.append(_("Room {0} does not exist.").format(room_num))
            continue
            
        if not r.is_enabled:
            conflicts.append(_("Room {0} is currently disabled/under maintenance.").format(room_num))
        elif r.status == "Out of Order":
            conflicts.append(_("Room {0} is marked Out of Order.").format(room_num))

    # 2. Check Overlapping Reservations for all rooms in batch
    # Logic: New Arrival < Existing Departure AND New Departure > Existing Arrival
    existing_bookings = frappe.get_all("Hotel Reservation", 
        filters={
            "room": ["in", rooms],
            "status": ["in", ["Reserved", "Checked In"]],
            "name": ["!=", ignore_reservation] if ignore_reservation else ["is", "set"]
        },
        fields=["name", "arrival_date", "departure_date", "guest", "room"]
    )

    for booking in existing_bookings:
        # Check for date overlap
        if (getdate(arrival_date) < getdate(booking.departure_date)) and \
           (getdate(departure_date) > getdate(booking.arrival_date)):
             conflicts.append(
                _("Room {0} is already booked by {1} from {2} to {3} (Reservation: {4})").format(
                    booking.room, booking.guest, booking.arrival_date, booking.departure_date, booking.name
                )
             )

    if conflicts:
        message = _("The following availability issues were found:") + "<br><br>"
        message += "<div class='alert alert-danger'>"
        message += "<ul>"
        for conflict in conflicts:
            message += f"<li>{conflict}</li>"
        message += "</ul>"
        message += "</div>"
        frappe.throw(message, title=_("Room Availability Conflict"))

    return True

@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_rooms_for_picker(doctype, txt, searchfield, start, page_len, filters):
    """
    Search picker for rooms that are available for the selected dates.
    Filters: arrival_date, departure_date, room_type, ignore_reservation
    """
    filters = frappe.parse_json(filters)
    arrival = filters.get("arrival_date")
    departure = filters.get("departure_date")
    room_type = filters.get("room_type")
    ignore = filters.get("ignore_reservation")

    if not arrival or not departure:
        # If dates aren't set, return all rooms of that type (or all if no type)
        return frappe.db.sql("""
            SELECT name, room_type, status
            FROM `tabHotel Room`
            WHERE is_enabled = 1
            AND (%s IS NULL OR room_type = %s)
            AND name LIKE %s
            ORDER BY name ASC
            LIMIT %s, %s
        """, (room_type, room_type, f"%{txt}%", start, page_len))

    # Complex Query: Find rooms that are NOT in the overlapping reservations set
    return frappe.db.sql("""
        SELECT name, room_type, status
        FROM `tabHotel Room`
        WHERE is_enabled = 1
        AND status != 'Out of Order'
        AND (%s IS NULL OR room_type = %s)
        AND name LIKE %s
        AND name NOT IN (
            SELECT room FROM `tabHotel Reservation`
            WHERE status IN ('Reserved', 'Checked In')
            AND name != %s
            AND arrival_date < %s
            AND departure_date > %s
        )
        ORDER BY name ASC
        LIMIT %s, %s
    """, (room_type, room_type, f"%{txt}%", ignore or "", departure, arrival, start, page_len))

@frappe.whitelist()
def walk_in_check_in(
    full_name: str,
    room: str,
    hotel_reception: str | None = None,
    mobile_no: str | None = None,
    email_id: str | None = None,
    identification_type: str | None = None,
    identification_no: str | None = None,
    departure_date: str | None = None,
    rate_plan: str | None = None,
    is_day_use: int = 0,
) -> dict:
    """Create + immediately check-in a walk-in guest.

    Finds or creates the Guest by identification_no -> mobile_no -> email,
    validates room availability, opens the Hotel Reservation in Checked In
    status, and triggers folio creation via the standard after_insert hook.
    """
    if not frappe.has_permission("Hotel Reservation", "create"):
        frappe.throw(_("Not authorised to create reservations."))

    if not full_name or not room:
        frappe.throw(_("full_name and room are mandatory."))

    arrival_date = nowdate()
    if is_day_use and not departure_date:
        departure_date = add_days(arrival_date, 1)
    elif not departure_date:
        departure_date = add_days(arrival_date, 1)

    guest = _find_or_create_guest(
        full_name=full_name,
        mobile_no=mobile_no,
        email_id=email_id,
        identification_type=identification_type,
        identification_no=identification_no,
    )

    check_availability(
        room=room,
        arrival_date=arrival_date,
        departure_date=departure_date,
    )

    if not hotel_reception:
        hotel_reception = frappe.db.get_value("Hotel Room", room, "hotel_reception")

    res = frappe.new_doc("Hotel Reservation")
    res.guest = guest
    res.room = room
    res.room_type = frappe.db.get_value("Hotel Room", room, "room_type")
    res.hotel_reception = hotel_reception
    res.arrival_date = arrival_date
    res.departure_date = departure_date
    res.rate_plan = rate_plan
    res.status = "Checked In"
    res.flags.is_walk_in = 1
    res.insert()

    return {
        "reservation": res.name,
        "guest": guest,
        "room": room,
        "arrival_date": arrival_date,
        "departure_date": departure_date,
        "is_day_use": bool(is_day_use),
    }


def _find_or_create_guest(
    full_name: str,
    mobile_no: str | None = None,
    email_id: str | None = None,
    identification_type: str | None = None,
    identification_no: str | None = None,
) -> str:
    """Resolve an existing Guest by identification > mobile > email,
    otherwise create a new one. Returns Guest.name."""
    if identification_no:
        existing = frappe.db.get_value("Guest", {"identification_no": identification_no}, "name")
        if existing:
            return existing
    if mobile_no:
        existing = frappe.db.get_value("Guest", {"mobile_no": mobile_no}, "name")
        if existing:
            return existing
    if email_id:
        existing = frappe.db.get_value("Guest", {"email_id": email_id}, "name")
        if existing:
            return existing

    guest = frappe.new_doc("Guest")
    guest.full_name = full_name
    guest.mobile_no = mobile_no
    guest.email_id = email_id
    guest.identification_type = identification_type
    guest.identification_no = identification_no
    guest.guest_type = "Regular"
    guest.insert(ignore_permissions=True)
    return guest.name


@frappe.whitelist()
def list_arrivals_today(hotel_reception: str | None = None) -> list[dict]:
    filters = {
        "arrival_date": nowdate(),
        "status": ["in", ["Reserved", "Checked In"]],
    }
    if hotel_reception:
        filters["hotel_reception"] = hotel_reception
    return frappe.get_all(
        "Hotel Reservation",
        fields=["name", "guest", "room", "room_type", "arrival_date", "departure_date", "status"],
        filters=filters,
        order_by="creation asc",
    )


@frappe.whitelist()
def list_departures_today(hotel_reception: str | None = None) -> list[dict]:
    filters = {"departure_date": nowdate(), "status": "Checked In"}
    if hotel_reception:
        filters["hotel_reception"] = hotel_reception
    return frappe.get_all(
        "Hotel Reservation",
        fields=["name", "guest", "room", "room_type", "arrival_date", "departure_date"],
        filters=filters,
        order_by="creation asc",
    )


def create_folio(reservation_doc):
    """
    Creates a 'Provisional' Guest Folio linked to this reservation.
    """
    if frappe.db.exists("Guest Folio", {"reservation": reservation_doc.name, "status": ["!=", "Cancelled"]}):
        return

    folio = frappe.new_doc("Guest Folio")
    folio.guest = reservation_doc.guest
    folio.reservation = reservation_doc.name
    folio.room = reservation_doc.room
    folio.status = "Provisional"
    folio.company = reservation_doc.company # If corporate booking
    folio.hotel_reception = reservation_doc.hotel_reception
    folio.reserved_by = reservation_doc.reserved_by
    folio.open_date = nowdate()
    
    # Save the Folio
    folio.insert(ignore_permissions=True)
    
    # Link Folio back to Reservation
    reservation_doc.db_set("folio", folio.name)
    frappe.msgprint(_("Guest Folio {0} created successfully.").format(folio.name))

    # Transfer existing balances from the Guest Balance Ledger
    from hospitality_core.hospitality_core.api.folio import transfer_existing_balances
    transfer_existing_balances(folio)