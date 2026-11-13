"""
MacroOps AI - Synthetic Data Generator configuration.

Authority:  PRD V1.0 (business/product)  +  Data Dictionary V1.1 (schema).

EVERY value in this file is a [Synthetic Generation Assumption] or [Proposed] generation
parameter taken from Data Dictionary V1.1 Section 8. None of them is a business
requirement, and none of them resolves a PRD Open Question (OQ-01 ... OQ-11).
Change a value here and re-run; nothing else needs editing.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ----------------------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
VALIDATION_DIR = ROOT / "validation"

# ----------------------------------------------------------------------------------------
# Reproducibility, calendar and time zone   [Proposed]
# ----------------------------------------------------------------------------------------
SEED = 42
TZ_LABEL = "Asia/Kolkata (IST, UTC+05:30)"
TZ = timezone(timedelta(hours=5, minutes=30))
START_DATE = datetime(2026, 10, 1, tzinfo=TZ)           # 14 days: 2026-10-01 .. 2026-10-14
N_DAYS = 14
SNAPSHOT_TIME = datetime(2026, 10, 14, 19, 30, tzinfo=TZ)  # "as-of" time for stage/SLA state

# ----------------------------------------------------------------------------------------
# Volumes   [Proposed]
# ----------------------------------------------------------------------------------------
N_STORES = 6
N_ORDERS = 1200
N_SKUS = 40                                  # 40 SKUs x 6 stores = 240 inventory rows
LINES_PER_ORDER_WEIGHTS = {1: .15, 2: .25, 3: .25, 4: .18, 5: .10, 6: .07}   # 1-6 lines
QTY_WEIGHTS = {1: .55, 2: .25, 3: .12, 4: .08}                               # units per line
STORE_WEIGHTS = [.22, .20, .18, .16, .14, .10]                               # order share/store (len = N_STORES)

# Orders are placed 07:00-22:59; peak hours carry double weight   [Proposed]
OPERATING_HOURS = range(7, 23)
PEAK_HOUR_WEIGHT = {12: 2.0, 13: 2.0, 18: 2.0, 19: 2.0, 20: 2.0}   # 12:00-14:00 and 18:00-21:00

# ----------------------------------------------------------------------------------------
# SLA logic   [Synthetic Generation Assumption]  (OQ-01 / OQ-02 remain open in the PRD)
# ----------------------------------------------------------------------------------------
PROMISED_WINDOW_MINUTES = 30
AT_RISK_BUFFER_MINUTES = 10

# ----------------------------------------------------------------------------------------
# Scenario mix   [Proposed]
# EXCEPTION_RATE = share of orders that carry an operational problem scenario
# (delay scenarios + stockout + failed delivery). Cancelled orders are counted separately.
# ----------------------------------------------------------------------------------------
EXCEPTION_RATE = 0.15
CANCELLATION_RATE = 0.03
FAILED_DELIVERY_RATE = 0.02
FAILED_RETRY_SUCCESS_SHARE = 0.60            # 60% of failed deliveries succeed on the single retry
# How the non-failed problem share (EXCEPTION_RATE - FAILED_DELIVERY_RATE) is split:
PROBLEM_SPLIT = {
    "picking_delay": 0.20, "packing_delay": 0.15, "rider_shortage": 0.20,
    "delivery_delay": 0.20, "multi_stage": 0.10, "stockout": 0.15,
}
MISPICK_RATE = 0.015                         # share of NORMAL orders with a 1-unit mis-pick

# Live (in-progress at snapshot_time) orders so that At Risk / Breached states exist   [SGA]
LIVE_ORDERS = 24
LIVE_WINDOW_MINUTES = 50
LIVE_FORCED = [                              # (scenario, targeted SLA state at snapshot)
    ("picking_delay", "atrisk"), ("picking_delay", "atrisk"), ("packing_delay", "any"),
    ("rider_shortage", "atrisk"), ("rider_shortage", "breached"),
    ("delivery_delay", "breached"), ("delivery_delay", "breached"),
    ("multi_stage", "breached"), ("multi_stage", "atrisk"),
    ("stockout", "any"), ("failed_retry", "any"), ("failed_noretry", "any"),
]

# ----------------------------------------------------------------------------------------
# Thresholds used ONLY to label synthetic delay exceptions (not validated detection rules; OQ-03)
# ----------------------------------------------------------------------------------------
PICK_DELAY_MIN = 10
PACK_DELAY_MIN = 6
ASSIGN_DELAY_MIN = 5

# ----------------------------------------------------------------------------------------
# Pools   [Synthetic Generation Assumption]  (no workforce dataset: OQ-09 open)
# ----------------------------------------------------------------------------------------
ASSOCIATES_PER_STORE = 8
RIDERS_PER_STORE = 8
MANAGERS_PER_STORE = 2

# ----------------------------------------------------------------------------------------
# Inventory   [SGA]
# ----------------------------------------------------------------------------------------
STOCKOUT_COMBOS = 10          # store/SKU rows with counted_shelf_stock in {0, 1} (system shows more)
EXTRA_MISMATCH_ROWS = 14      # additional system-vs-shelf mismatches
LOW_STOCK_ROWS = 20           # healthy-but-low rows (3-10 units)
MISMATCH_EXCEPTION_PROB = 0.70

# ----------------------------------------------------------------------------------------
# Exceptions   [SGA]
# ----------------------------------------------------------------------------------------
APPROVAL_APPLICABLE_SHARE = 0.40
DECISION_WEIGHTS = {"Approved": 0.70, "Edited": 0.15, "Rejected": 0.15}
SLA_EXCEPTION_PROB = 0.40     # prob. that an At Risk / Breached order also gets an SLA Risk/Breach exception

# ----------------------------------------------------------------------------------------
# SOP knowledge   [Proposed]
# ----------------------------------------------------------------------------------------
N_SOP_DOCS = 12
SECTIONS_PER_DOC = 3          # -> 36 sections

# ----------------------------------------------------------------------------------------
# Allowed values (Data Dictionary V1.1)
# ----------------------------------------------------------------------------------------
CATEGORIES = ["Inventory Mismatch", "Stockout", "Picking Delay", "Packing Delay",
              "Rider Assignment Delay", "Delivery Delay", "SLA Risk/Breach",
              "Failed Delivery Attempt"]
ENUMS = {
    ("stores", "status"): ["Active", "Inactive"],
    ("orders", "current_stage"): ["Placed", "Picking", "Packing", "Awaiting Rider",
                                  "Out for Delivery", "Delivered", "Cancelled"],
    ("orders", "order_status"): ["Placed", "In Progress", "Delivered", "Cancelled", "Delivery Failed"],
    ("orders", "sla_status"): ["On Time", "At Risk", "Breached", "Not Applicable"],
    ("delivery_rider", "delivery_status"): ["Awaiting Assignment", "Assigned", "Picked Up",
                                            "In Transit", "Delivered", "Failed"],
    ("delivery_rider", "rider_availability"): ["Available", "Busy"],
    ("exceptions", "category"): CATEGORIES,
    ("exceptions", "status"): ["Open", "Under Investigation", "Pending Approval", "Resolved/Closed"],
    ("exceptions", "approval_decision"): ["Approved", "Edited", "Rejected"],
    ("sop_knowledge", "approval_status"): ["Approved", "Draft", "Retired"],
    ("sop_knowledge", "exception_category"): CATEGORIES,
    ("sop_knowledge", "content_origin"): ["Synthetic"],
    ("scenario_labels", "scenario_type"): [
        "Normal flow", "Picking delay", "Packing delay", "Inventory mismatch", "Stockout",
        "Rider shortage", "Delivery delay", "Failed delivery", "Multi-stage disruption",
        "Live At Risk / Breached", "Cancelled"],
}

# ----------------------------------------------------------------------------------------
# Schema (exact field names / order from Data Dictionary V1.1)
# types: str | text | int | bool | dt
# ----------------------------------------------------------------------------------------
FILES = {
    "stores": "stores.csv", "orders": "orders.csv", "order_items": "order_items.csv",
    "inventory": "inventory.csv", "picking": "picking.csv", "packing": "packing.csv",
    "delivery_rider": "delivery_rider.csv", "rider_availability": "rider_availability.csv",
    "exceptions": "exceptions.csv", "sop_knowledge": "sop_knowledge.csv",
    "scenario_labels": "scenario_labels.csv",
}
SCHEMA = {
    "stores": [("store_id", "str"), ("store_name", "str"), ("zone", "str"),
               ("pincode", "str"), ("status", "str")],
    "orders": [("order_id", "str"), ("store_id", "str"), ("order_time", "dt"),
               ("promised_delivery_time", "dt"), ("current_stage", "str"), ("order_status", "str"),
               ("sla_status", "str"), ("pick_start", "dt"), ("pick_end", "dt"), ("pack_start", "dt"),
               ("pack_end", "dt"), ("assignment_time", "dt"), ("pickup_time", "dt"),
               ("delivery_time", "dt"), ("delivery_zone", "str"), ("delivery_pincode", "str"),
               ("item_count", "int"), ("total_quantity", "int")],
    "order_items": [("order_id", "str"), ("store_id", "str"), ("sku_id", "str"),
                    ("quantity_ordered", "int"), ("quantity_picked", "int"),
                    ("substitution_flag", "bool")],
    "inventory": [("store_id", "str"), ("sku_id", "str"), ("system_stock", "int"),
                  ("counted_shelf_stock", "int"), ("last_updated", "dt"),
                  ("adjustment_quantity", "int"), ("return_quantity", "int")],
    "picking": [("order_id", "str"), ("picker_associate_id", "str"), ("pick_start", "dt"),
                ("pick_end", "dt"), ("items_ordered", "int"), ("items_picked", "int"),
                ("mis_pick_flag", "bool")],
    "packing": [("order_id", "str"), ("pack_start", "dt"), ("pack_end", "dt"),
                ("packing_issue_flag", "bool")],
    "delivery_rider": [("order_id", "str"), ("rider_id", "str"), ("assignment_time", "dt"),
                       ("pickup_time", "dt"), ("delivery_time", "dt"), ("delivery_status", "str"),
                       ("failed_attempt_flag", "bool"), ("rider_availability", "str")],
    "rider_availability": [("store_id", "str"), ("snapshot_time", "dt"), ("riders_available", "int"),
                           ("riders_busy", "int"), ("riders_unavailable", "int")],
    "exceptions": [("exception_id", "str"), ("category", "str"), ("order_id", "str"),
                   ("store_id", "str"), ("sku_id", "str"), ("detected_at", "dt"), ("status", "str"),
                   ("assigned_to", "str"), ("approval_applicable", "bool"), ("approver", "str"),
                   ("approval_decision", "str"), ("approved_at", "dt"), ("resolution", "text"),
                   ("resolved_at", "dt")],
    "sop_knowledge": [("document_id", "str"), ("title", "str"), ("section", "str"), ("text", "text"),
                      ("version", "str"), ("owner", "str"), ("approval_status", "str"),
                      ("exception_category", "str"), ("content_origin", "str")],
    "scenario_labels": [("label_id", "str"), ("scenario_type", "str"), ("order_id", "str"),
                        ("store_id", "str"), ("sku_id", "str"), ("root_cause_stage", "str")],
}
PRIMARY_KEYS = {
    "stores": ["store_id"], "orders": ["order_id"], "order_items": ["order_id", "sku_id"],
    "inventory": ["store_id", "sku_id"], "picking": ["order_id"], "packing": ["order_id"],
    "delivery_rider": ["order_id"], "rider_availability": ["store_id", "snapshot_time"],
    "exceptions": ["exception_id"], "sop_knowledge": ["document_id", "section"],
    "scenario_labels": ["label_id"],
}
