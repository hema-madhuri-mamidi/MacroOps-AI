"""
Synthetic SOP knowledge for the MacroOps AI prototype RAG experiments.

All text below is ORIGINAL synthetic content written for this prototype. It is not taken from
any company, it does not describe a real organization's procedures, and it does not decide any
PRD Open Question (for example which actions need approval, OQ-06). Thresholds are read from
config.py and are synthetic generation assumptions.
"""
import config as C

PT, KT, AT = C.PICK_DELAY_MIN, C.PACK_DELAY_MIN, C.ASSIGN_DELAY_MIN
WIN, BUF = C.PROMISED_WINDOW_MINUTES, C.AT_RISK_BUFFER_MINUTES

# (document_id, title, exception_category, owner, version, [section texts], {section_index: status})
DOCS = [
    ("SOP_001", "Inventory Mismatch Handling", "Inventory Mismatch",
     "Inventory Control Lead (synthetic)", "1.0", [
        "An inventory mismatch exists when system stock differs from the physically counted shelf stock for a store and SKU. Log an Inventory Mismatch exception as soon as a count differs from the system value.",
        "Recount the SKU at its shelf location, check recent returns and stock adjustments, and record the counted quantity. If the difference is confirmed, prepare a stock adjustment for review before the system value is changed.",
        "If the same SKU shows a mismatch more than once in a shift, escalate to the Fulfillment Manager. A stock adjustment that requires approval stays in Pending Approval until a manager records a decision.",
     ], {}),
    ("SOP_002", "Stockout and Short-Pick Handling", "Stockout",
     "Inventory Control Lead (synthetic)", "1.0", [
        "A stockout is recorded when an order line is picked short because the shelf holds fewer units than ordered. Log a Stockout exception against the order and the SKU.",
        "Confirm the shelf count, pick the units that are available, and mark whether a substitution was offered. Do not pick from another SKU unless a substitution has been recorded for the order line.",
        "Repeated stockouts on the same SKU are reported to the Fulfillment Manager for a replenishment check. Record the action taken before the exception is closed.",
     ], {}),
    ("SOP_003", "Picking Delay Response", "Picking Delay",
     "Store Operations Lead (synthetic)", "1.0", [
        f"A picking delay is raised when picking for an order runs longer than {PT} minutes. In this prototype the {PT}-minute value is a synthetic generation assumption, not a validated rule.",
        "Check the picker workload, the number of lines in the order, and any stock problems at the shelf. Move the order to a free picker where possible and note the likely cause.",
        f"If the delay will push the order toward its promised time ({WIN} minutes in this prototype), flag the order to the Fulfillment Manager so the downstream stages can be prepared.",
     ], {}),
    ("SOP_004", "Packing Delay Response", "Packing Delay",
     "Store Operations Lead (synthetic)", "1.0", [
        f"A packing delay is raised when packing for an order runs longer than {KT} minutes. The value is a synthetic generation assumption.",
        "Check for packing material shortages, an item that needs re-checking, or a packing station backlog. Record any packing issue so it can be reviewed later.",
        "Draft guidance: when several packing delays occur in the same hour, the manager may re-balance packing staff between stations. This section is not yet approved.",
     ], {2: "Draft"}),
    ("SOP_005", "Rider Shortage and Assignment Delay Response", "Rider Assignment Delay",
     "Delivery Operations Lead (synthetic)", "1.0", [
        f"A rider assignment delay is raised when a packed order waits more than {AT} minutes for a rider. The value is a synthetic generation assumption.",
        "Check how many riders are available, busy or unavailable for the store. Hold the order at the dispatch point and keep the packed order protected from handling delays.",
        "If no rider is available, notify the Delivery Operations Manager. Any decision to bring in additional riders is taken by the manager and recorded against the exception.",
     ], {}),
    ("SOP_006", "Delivery Delay Response", "Delivery Delay",
     "Delivery Operations Lead (synthetic)", "2.0", [
        "A delivery delay is recorded when an order is delivered after its promised time, or is still undelivered once that time has passed. The cause may sit in an earlier stage.",
        "Trace the order back through picking, packing and rider assignment to find the earliest stage that deviated. Record the likely cause as a candidate cause, not as a confirmed fact.",
        "Older version 1.0 of this guidance asked associates to phone the customer for every delay. It has been replaced by version 2.0 and is retired.",
     ], {2: "Retired"}),
    ("SOP_007", "SLA At Risk and Breach Response", "SLA Risk/Breach",
     "Fulfillment Manager (synthetic)", "1.0", [
        f"In this prototype an order is At Risk when it is not yet delivered and no more than {BUF} minutes remain before its promised time. It is Breached when the promised time has passed without delivery. These are synthetic assumptions.",
        "Open the order detail, review the current stage and the time spent in it, and check related exceptions. Prioritise the stage that is holding the order.",
        "Record the decision taken for an At Risk or Breached order. Where the action needs human approval, the exception waits in Pending Approval until a manager decides.",
     ], {}),
    ("SOP_008", "Failed Delivery Attempt Handling", "Failed Delivery Attempt",
     "Delivery Operations Lead (synthetic)", "1.0", [
        "A failed delivery attempt is recorded when the rider cannot hand the order to the customer. The delivery stays open and the failed attempt is logged.",
        "In this prototype the rider makes at most one retry. Contact the customer through the approved channel, note the reason for the failure and record the retry time.",
        "If the retry also fails, the order is marked Delivery Failed and the Delivery Operations Manager decides on the next step. The decision is recorded on the exception.",
     ], {}),
    ("SOP_009", "Exception Logging and Approval Guidelines", None,
     "Store Operations Lead (synthetic)", "1.1", [
        "Every operational exception is logged with a category, the affected order or store, and the time it was detected. Use the category list that is currently in use; it is a proposal and may change.",
        "An exception moves through Open, Under Investigation, Pending Approval where an approval is needed, and Resolved/Closed. Record the resolution in plain words before closing.",
        "Whether an action requires approval is decided by store management. Where approval applies, the approver records Approved, Edited or Rejected together with the time of the decision.",
     ], {}),
    ("SOP_010", "Peak Hour Operating Guidelines", None,
     "Store Operations Lead (synthetic)", "1.0", [
        "Peak hours are the busiest order periods of the day, typically around midday and in the evening in this synthetic scenario. Plan staffing and rider cover before they start.",
        "Draft guidance: stagger breaks for pickers and packers and keep a rider on standby near the dispatch point. This section is still under review.",
        "During peak hours check the dashboard for orders that are At Risk and for open exceptions at least once every few minutes.",
     ], {1: "Draft"}),
    ("SOP_011", "Shift Handover Checklist", None,
     "Store Operations Lead (synthetic)", "1.0", [
        "At the end of a shift the outgoing associate lists open exceptions, orders still in progress and any stock problems found during the shift.",
        "The incoming associate reads the list, confirms who owns each open exception and asks about anything unclear before starting work.",
        "Handover notes are kept in the shift log. Open exceptions that are Pending Approval are named to the manager on duty.",
     ], {}),
    ("SOP_012", "Order Cancellation Handling", None,
     "Customer Operations Lead (synthetic)", "1.0", [
        "An order may be cancelled before picking starts. A cancelled order has no picking, packing or delivery record and is not measured against its promised time.",
        "Older guidance allowed cancellation after packing had started. This guidance is no longer used and has been retired.",
        "Record the cancellation time and the reason. If stock was reserved for the order, release it so that the shelf count stays accurate.",
     ], {1: "Retired"}),
]
