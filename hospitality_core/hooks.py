app_name = "hospitality_core"
app_title = "Hospitality Core"
app_publisher = "Gift Braimah"
app_description = "Hotel Management Module"
app_email = "braimahgifted@gmail.com"
app_license = "gpl-2.0"

add_to_apps_screen = [
    {
        "name": app_name,
        "logo": "/assets/hospitality_core/images/hospitality-core-logo.svg",
        "title": app_title,
        "route": "/app/hospitality",
    }
]

app_include_js = [
    "/assets/hospitality_core/js/hospitality_analytics_final.js",
    "/assets/hospitality_core/js/pos_room_selection.js",
    "/assets/hospitality_core/js/pos_invoice_auto_print.js",
    "/assets/hospitality_core/js/payment_entry_auto_print.js"
]

app_include_css = [
    "/assets/hospitality_core/css/print_format.css"
]


# Multi-tenant scoping by Hotel Reception (User Permission driven)
permission_query_conditions = {
    "Hotel Room": "hospitality_core.hospitality_core.permissions.hotel_room_query",
    "Hotel Reservation": "hospitality_core.hospitality_core.permissions.hotel_reservation_query",
    "Guest Folio": "hospitality_core.hospitality_core.permissions.guest_folio_query",
    "Hospitality Expense": "hospitality_core.hospitality_core.permissions.hospitality_expense_query",
    "Folio Transaction": "hospitality_core.hospitality_core.permissions.folio_transaction_query",
    "Housekeeping Task": "hospitality_core.hospitality_core.permissions.housekeeping_task_query",
    "Concierge Request": "hospitality_core.hospitality_core.permissions.concierge_request_query",
    "Hospitality Loyalty Entry": "hospitality_core.hospitality_core.permissions.loyalty_entry_query",
    "Minibar Consumption": "hospitality_core.hospitality_core.permissions.minibar_consumption_query",
}

# Document Events
doc_events = {
    "Hotel Reservation": {
        "on_submit": [
            "hospitality_core.hospitality_core.utils.audit.hook_log_reservation",
            "hospitality_core.hospitality_core.utils.notifications.hook_reservation_status"
        ],
        "on_cancel": "hospitality_core.hospitality_core.utils.audit.hook_log_reservation",
        "on_update_after_submit": [
            "hospitality_core.hospitality_core.utils.audit.hook_log_reservation",
            "hospitality_core.hospitality_core.api.housekeeping.hook_on_reservation_checkout",
            "hospitality_core.hospitality_core.utils.notifications.hook_reservation_status",
            "hospitality_core.hospitality_core.api.loyalty.hook_on_reservation_checkout"
        ]
    },
    "Guest Folio": {
        "on_update": [
            "hospitality_core.hospitality_core.api.folio.sync_folio_balance",
            "hospitality_core.hospitality_core.utils.audit.hook_log_folio"
        ]
    },
    "Folio Transaction": {
        "after_save": [
            "hospitality_core.hospitality_core.api.folio.sync_folio_balance",
            "hospitality_core.hospitality_core.api.accounting.make_gl_entries_for_folio_transaction",
            "hospitality_core.hospitality_core.utils.audit.hook_log_folio_transaction"
        ],
        "on_trash": "hospitality_core.hospitality_core.api.folio.sync_folio_balance"
    },
    "Hospitality Expense": {
        "on_submit": "hospitality_core.hospitality_core.utils.audit.hook_log_expense",
        "on_cancel": "hospitality_core.hospitality_core.utils.audit.hook_log_expense"
    },
    "POS Invoice": {
        "on_submit": [
            "hospitality_core.hospitality_core.api.pos_bridge.process_room_charge",
            "hospitality_core.hospitality_core.api.accounting.redirect_pos_income_to_suspense",
            "hospitality_core.hospitality_core.api.accounting.reclassify_pos_taxes",
            "hospitality_core.api.composite_item_utils.process_composite_items_in_invoice"
        ],
        "on_cancel": [
            "hospitality_core.hospitality_core.api.pos_bridge.void_room_charge",
            "hospitality_core.hospitality_core.api.accounting.redirect_pos_income_to_suspense",
            "hospitality_core.hospitality_core.api.accounting.reclassify_pos_taxes",
            "hospitality_core.api.composite_item_utils.process_composite_items_in_invoice"
        ]
    },
    "Payment Entry": {
        "on_submit": [
            "hospitality_core.hospitality_core.api.payment_bridge.process_payment_entry",
            "hospitality_core.hospitality_core.utils.audit.hook_log_payment",
            "hospitality_core.hospitality_core.utils.notifications.hook_payment_receipt"
        ],
        "on_cancel": [
            "hospitality_core.hospitality_core.api.payment_bridge.process_payment_entry",
            "hospitality_core.hospitality_core.utils.audit.hook_log_payment"
        ]
    },
    "Sales Invoice": {
        "on_submit": "hospitality_core.api.composite_item_utils.process_composite_items_in_invoice",
        "on_cancel": "hospitality_core.api.composite_item_utils.process_composite_items_in_invoice"
    }
}

after_install = "hospitality_core.setup.after_install"
after_migrate = ["hospitality_core.hospitality_core.utils.notifications.ensure_email_templates"]

# Scheduled Tasks
# changed daily audit to run at 2 PM (14:00) per requirements
scheduler_events = {
    "cron": {
        "0 14 * * *": [
            "hospitality_core.hospitality_core.api.night_audit.run_daily_audit"
        ]
    }
}

# Fixtures
fixtures = [
    {"dt": "Custom Field", "filters": [["module", "=", "Hospitality Core"]]},
    {"dt": "Property Setter", "filters": [["module", "=", "Hospitality Core"]]},
    {"dt": "Email Template", "filters": [["name", "in", [
        "Hospitality - Booking Confirmation",
        "Hospitality - Check-in Welcome",
        "Hospitality - Check-out Thanks",
        "Hospitality - Payment Receipt",
    ]]]}
]