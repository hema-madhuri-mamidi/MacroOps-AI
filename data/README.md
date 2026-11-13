# MacroOps AI – Synthetic Operational Dataset

## 1. What the dataset represents
A connected, 14-day synthetic record of fulfilment and last-mile operations for the proposed
**MacroOps AI** platform: 6 synthetic stores, 1,200 orders, SKU-level order lines, inventory,
picking, packing, rider assignment and delivery, hourly rider availability, operational
exceptions, a small SOP knowledge base and a scenario-label reference file.

## 2. Synthetic / non-production
Every value is synthetic. No real customer, employee, rider, company, store or locality
information is used. Zones, pincodes and names are synthetic labels. The numbers are
**prototype assumptions, not real operational benchmarks**.

## 3. Source documents
* MacroOps AI PRD V1.0 – business/product authority.
* MacroOps AI – Synthetic Data Specification & Data Dictionary V1.1 – schema authority
  (field names, allowed values, relationships, validation rules, generation assumptions).

## 4. Datasets (data/raw)
| File | Class | Rows | Purpose |
|---|---|---:|---|
| stores.csv | Supporting | 6 | Store reference |
| orders.csv | Core | 1,200 | Order, promise, stage, status, SLA state, copied stage timestamps |
| order_items.csv | Supporting | 3,657 | SKU lines per order (bridge to inventory) |
| inventory.csv | Core | 240 | Store/SKU stock snapshot, system vs shelf |
| picking.csv | Core | 1,164 | Picking activity |
| packing.csv | Core | 1,161 | Packing activity |
| delivery_rider.csv | Core | 1,159 | Rider assignment and delivery |
| rider_availability.csv | Supporting Synthetic (Proposed) | 1,404 | Hourly store-level rider supply |
| exceptions.csv | Core | 362 | Detected operational issues and workflow state |
| sop_knowledge.csv | Core (synthetic content) | 36 | 12 SOP documents x 3 sections for RAG tests |
| scenario_labels.csv | Synthetic Reference | 1,225 | Ground-truth scenario labels (derived from observed facts; see section 9a) |

No workforce/associate master dataset exists (OQ-09 is open). Associate, rider and manager IDs
come from fixed synthetic pools per store.

## 5. Relationships
Order -> Picking -> Packing -> Delivery all join on `order_id` (one record per order that
reached the stage). `order_items.store_id + sku_id` -> `inventory`. `exceptions.order_id` ->
orders (null for store/SKU-level inventory exceptions, which use `store_id + sku_id`).
`exceptions.category` = `sop_knowledge.exception_category`. `rider_availability` joins
stores by `store_id` and hour. `scenario_labels` point at orders or store/SKU rows.
Stage timestamps duplicated in `orders` are exact copies of the stage-table values.

## 6. Configuration
All parameters are in `generator/config.py`: seed, date range (2026-10-01 to 2026-10-14,
Asia/Kolkata), snapshot time (2026-10-14 19:30 IST), stores, orders, SKUs, lines per order,
peak hours, promised window (30 min), At Risk buffer (10 min), scenario mix, exception,
cancellation and failed-delivery rates, delay thresholds, pools, SOP counts.

## 7. Random seed
`SEED = 42`. The same configuration and seed give byte-identical CSVs. Checksums of the
delivered files are in `validation/checksums_sha256.txt`.

## 8. How to run
```
pip install -r requirements.txt
python generator/generate_data.py
```
Output: `data/raw/*.csv`, `validation/validation_report.txt`, `validation/checksums_sha256.txt`.
The script exits with an error if any critical validation check fails.

## 9. How validation works
`generator/validators.py` re-reads the written CSVs and runs 224 checks (152 integrity/coverage checks plus 72 semantic scenario checks, section 9a): exact field names,
primary-key uniqueness, allowed values, foreign keys and pools, stage-existence rules,
exact timestamp equality between Orders and stage tables, lifecycle ordering, SLA
re-derivation, quantity reconciliation, status mappings, conditional nullability, failed-
delivery rules, exception timing and approval rules, agreement between exceptions and the
underlying timestamps, inventory/rider/SOP consistency, scenario coverage and scenario semantics.

## 9a. How scenario labels are assigned (ground-truth labelling convention)
Labels are **derived from the finished tables**, after the operational records exist. The intended
scenario used to build an order is never copied into `scenario_labels`. A regular (non-live) order whose
intended scenario would be cut off by the snapshot is resampled; if a regular order still disagrees with
its derived label the generator stops with an error instead of writing it.

Signatures (Data Dictionary V1.1 Section 7; thresholds in `config.py`): Normal flow = Delivered, delivery_time
<= promise, no exception. Picking delay = pick_start/pick_end exist and duration > `PICK_DELAY_MIN`, later
stages normal. Packing delay = picking normal, pack duration > `PACK_DELAY_MIN`. Rider shortage =
pack_end to assignment wait > `ASSIGN_DELAY_MIN` with `riders_available = 0` in an hourly snapshot covering
that window. Delivery delay = rider assigned, delivery_time populated and > promise (an undelivered breach is
NOT a Delivery delay). Failed delivery = `failed_attempt_flag`, either retry-success (Delivered) or
no-success (Failed / Delivery Failed). Multi-stage disruption = picking delay + packing delay + rider wait +
late delivery, all observed (root cause Picking). Stockout = short-picked line with
`counted_shelf_stock < quantity_ordered`. Inventory mismatch = `system_stock != counted_shelf_stock`.
Cancelled = Cancelled before picking, no stage records.

**Precedence (root-cause hierarchy, one primary label per order):** Cancelled > Stockout >
(Multi-stage | Picking delay) > Packing delay > Rider shortage > Failed delivery > Delivery delay > Normal flow.
The earliest abnormal stage owns the label, so an order that satisfies several signatures (for example a
picking delay that also lands late) gets exactly one primary label. **Live At Risk / Breached** is an
overlay on top of that: it is added for every order whose `order_status` is `In Progress` or `Placed` and
whose `sla_status` is At Risk or Breached. Inventory mismatch labels are store/SKU level (no order).

**Documented assumptions (not changes to the Data Dictionary):**
* *Snapshot truncation.* A label is assigned only if its signature is observable at `snapshot_time`. An order
  whose picking-origin chain is only partly visible gets the scenario that is observable (e.g. only a long
  pick so far = Picking delay) and never a complete Multi-stage disruption. If the chain is part-visible but
  matches no single-stage definition (picking, packing and rider delays seen, delivery not yet recorded),
  it has no primary label and carries only the Live overlay when At Risk/Breached. Such orders, and in-flight
  orders that are still On Time, have no primary label; no delivered or cancelled order is unlabelled.
* *"In progress".* `Delivery Failed` is a distinct `order_status` and is not "in progress", so those orders
  are not labelled Live At Risk / Breached (they carry Failed delivery). A failed attempt whose retry is still
  pending at the snapshot cannot be told apart from a final failure in this data.
* The rider-shortage window is the set of hourly snapshots overlapping pack_end to assignment_time.

## 10. Important synthetic assumptions
* SLA: clock runs from `order_time` to `delivery_time`; promise = order_time + 30 min; At Risk
  = undelivered with 0-10 min remaining at the snapshot; Breached = promise passed; cancelled
  = Not Applicable. (OQ-01, OQ-02 open.)
* The snapshot time is 2026-10-14 19:30 IST. Events after it do not exist yet, so 27 orders
  are undelivered and not cancelled at that time (16 In Progress, 11 Delivery Failed); 24 live orders are
  deliberately seeded so At Risk and Breached states exist.
* Delay exceptions are rule-based on timestamps: picking > 10 min, packing > 6 min, rider wait
  > 5 min, delivery later than promised. Thresholds are not validated rules (OQ-03).
* An order past its promise raises a Delivery Delay exception even when the cause is upstream,
  so one order can carry several exceptions (propagation). SLA Risk/Breach exceptions are
  raised for 40% of At Risk/Breached orders.
* Approval: 40% of exceptions have `approval_applicable = true`; this is a generation switch,
  not a statement of which actions need approval (OQ-06). Decisions: Approved/Edited/Rejected;
  a Rejected decision returns the exception to Under Investigation.
* Failed delivery: at most one retry by the same rider. Retry success -> Delivered with
  `failed_attempt_flag = true`; no success -> `Failed` / `Delivery Failed`, `delivery_time` null.
* Cancelled orders are cancelled before picking and have no stage records.
* IDs follow the Data Dictionary formats (ST001, AS-ST001-01, RD-ST001-01, MG-ST001-01),
  not the illustrative examples in the generation brief; order, SKU, exception and SOP IDs
  use ORD_, SKU_, EXC_, SOP_ prefixes.

## 11. Known limitations
* Inventory is a single snapshot, not a history, and is not decremented by orders.
  Inventory Mismatch exceptions stay unresolved so the mismatch is still visible.
* Rider availability is an hourly store-level synthetic snapshot. Shortage hours
  (`riders_available = 0`) are constructed around rider-shortage and multi-stage orders; other
  hours scale with order volume. It is not derived from each rider's schedule.
* A rider can hold overlapping orders (batching; labelled `Busy`): 21 cases.
* Mis-picks (about 1.5% of normal orders) set `mis_pick_flag` but raise no exception, since no
  mis-pick category exists. Substitutes are not modelled.
* At Risk is a live state at the snapshot; At Risk history is not stored.
* All stores are Active. No cancellation time, customer or address data exist.
* Exception history, recommendation feedback and user/role data are application-generated at
  runtime and are not part of this package.

## 12. Open questions that still depend on mentor/business validation
OQ-01 SLA definition, OQ-02 At Risk threshold, OQ-03 categories and detection rules (including
whether SLA Risk/Breach is a category), OQ-04 mandatory fields, OQ-06 approval requirements,
OQ-07 primary MVP KPIs, OQ-09 workforce/associate dataset, and acceptance of the supporting and
reference datasets.
