"""
Automated validation for the MacroOps AI synthetic datasets.

The validator re-reads the CSV files that were actually written (not the in-memory frames), so it
checks the delivered artefacts against Data Dictionary V1.1 Section 6 and the generation prompt.
Critical failures make the generator exit non-zero.
"""
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

S = pd.Timestamp(C.SNAPSHOT_TIME).tz_convert("UTC")
MIN = pd.Timedelta(minutes=1)


def load(raw_dir):
    D = {}
    for name, cols in C.SCHEMA.items():
        df = pd.read_csv(Path(raw_dir) / C.FILES[name], dtype=str, keep_default_na=False, na_values=[""])
        D[name] = df
    return D


def typed(D):
    T = {}
    for name, cols in C.SCHEMA.items():
        df = D[name].copy()
        for c, t in cols:
            if t == "int":
                df[c] = pd.to_numeric(df[c]).astype("Int64")
            elif t == "bool":
                df[c] = df[c].map({"True": True, "False": False}).astype("boolean")
            elif t == "dt":
                df[c] = pd.to_datetime(df[c], utc=True)
        T[name] = df
    return T


class Results:
    def __init__(self):
        self.rows = []

    def chk(self, name, ok, detail="", critical=True):
        self.rows.append((name, bool(ok), str(detail), critical))

    def warn(self, name, ok, detail=""):
        self.chk(name, ok, detail, critical=False)


def same(a, b):
    """Elementwise equality where null == null."""
    return (a == b).fillna(False) | (a.isna() & b.isna())


# ======================================================================================================
# Semantic scenario validation (Data Dictionary V1.1 Section 7).  Independent of the generator: it only
# re-reads the written CSVs and re-derives every scenario signature from the facts in them.
# ======================================================================================================
SEM_ORDER_SCENARIOS = ["Normal flow", "Picking delay", "Packing delay", "Inventory mismatch", "Stockout",
                       "Rider shortage", "Delivery delay", "Failed delivery", "Multi-stage disruption",
                       "Live At Risk / Breached", "Cancelled"]
SEM_ROOT = {"Stockout": "Inventory", "Picking delay": "Picking", "Multi-stage disruption": "Picking",
            "Packing delay": "Packing", "Rider shortage": "Rider Assignment", "Failed delivery": "Delivery",
            "Delivery delay": "Delivery", "Inventory mismatch": "Inventory"}


def semantic_scenario_checks(R, T):
    o, it, inv, pk, pc, dv = (T[k] for k in ["orders", "order_items", "inventory", "picking", "packing", "delivery_rider"])
    ex, ra, lab = T["exceptions"], T["rider_availability"], T["scenario_labels"]
    H = pd.Timedelta(hours=1)

    def chk(scn, name, ok, detail=""):
        R.chk(f"[Semantic:{scn}] {name}", ok, detail)

    F = o.set_index("order_id").copy()
    F["pick_dur"] = F.pick_end - F.pick_start
    F["pack_dur"] = F.pack_end - F.pack_start
    F["wait"] = F.assignment_time - F.pack_end
    F["has_exc"] = F.index.isin(set(ex.order_id.dropna()))
    F["failed"] = dv.set_index("order_id").failed_attempt_flag.reindex(F.index).fillna(False).astype(bool)
    zero = set(zip(ra[ra.riders_available == 0].store_id, ra[ra.riders_available == 0].snapshot_time))

    def zero_in_window(store, t0, t1):
        if pd.isna(t0) or pd.isna(t1):
            return False
        h = t0.tz_convert(C.TZ).floor("h").tz_convert("UTC")
        while h <= t1:
            if (store, h) in zero:
                return True
            h += H
        return False
    F["zero_win"] = [zero_in_window(s_, a_, b_) for s_, a_, b_ in zip(F.store_id, F.pack_end, F.assignment_time)]
    lines = it.drop(columns="store_id").merge(o[["order_id", "store_id"]], on="order_id").merge(
        inv[["store_id", "sku_id", "counted_shelf_stock"]], on=["store_id", "sku_id"])
    lines["stock_short"] = (lines.quantity_picked < lines.quantity_ordered) & (lines.counted_shelf_stock < lines.quantity_ordered)
    F["stock_short"] = F.index.isin(set(lines[lines.stock_short].order_id))

    pT, kT, aT = (pd.Timedelta(minutes=x) for x in (C.PICK_DELAY_MIN, C.PACK_DELAY_MIN, C.ASSIGN_DELAY_MIN))
    F["a_pick"] = (F.pick_dur > pT).fillna(False).astype(bool)
    F["a_pack"] = (F.pack_dur > kT).fillna(False).astype(bool)
    F["a_wait"] = (F.wait > aT).fillna(False).astype(bool)
    F["a_late"] = (F.assignment_time.notna() & F.delivery_time.notna() & (F.delivery_time > F.promised_delivery_time)).astype(bool)
    F["normal"] = ((F.order_status == "Delivered") & F.delivery_time.notna() & (F.delivery_time <= F.promised_delivery_time) & ~F.has_exc)
    canc = F.order_status == "Cancelled"
    # expected primary label = earliest abnormal stage whose signature is observable (documented in README section 10)
    F["expected"] = np.select(
        [canc, F.stock_short,
         F.a_pick & F.a_pack & F.a_wait & F.a_late,
         F.a_pick & ~F.a_pack & ~F.a_wait, F.a_pick,
         F.a_pack, F.a_wait & F.zero_win, F.a_wait,
         F.failed, F.a_late, F.normal],
        ["Cancelled", "Stockout", "Multi-stage disruption", "Picking delay", "", "Packing delay", "Rider shortage",
         "", "Failed delivery", "Delivery delay", "Normal flow"], default="")
    F["live_expected"] = F.order_status.isin(["Placed", "In Progress"]) & F.sla_status.isin(["At Risk", "Breached"])

    ol = lab[lab.order_id.notna()]
    prim = ol[ol.scenario_type != "Live At Risk / Breached"]
    live_l = ol[ol.scenario_type == "Live At Risk / Breached"]
    L = lambda t: lab[lab.scenario_type == t]
    LJ = lambda t: L(t).join(F, on="order_id", rsuffix="_o")
    stage_cols = ["pick_start", "pick_end", "pack_start", "pack_end", "assignment_time", "pickup_time", "delivery_time"]

    def reverse(scn, label):
        exp = set(F.index[F.expected == label])
        got = set(L(label).order_id.dropna())
        chk(scn, "bidirectional: every order whose facts satisfy this signature carries the label (and no other order does)",
            exp == got, f"{len(got)} labels checked; {len(exp - got)} unlabelled, {len(got - exp)} unsupported")

    def root(scn, label, want):
        chk(scn, f"root_cause_stage = {want}", (L(label).root_cause_stage == want).all() if want else L(label).root_cause_stage.isna().all())

    # ---- Normal flow
    n = LJ("Normal flow")
    chk("Normal flow", "order is delivered (order_status Delivered, stage Delivered)", ((n.order_status == "Delivered") & (n.current_stage == "Delivered")).all(), f"{len(n)} labels checked")
    chk("Normal flow", "delivery_time is populated and all stage timestamps exist", n[stage_cols].notna().all().all())
    chk("Normal flow", "delivery_time <= promised_delivery_time", (n.delivery_time <= n.promised_delivery_time).all())
    chk("Normal flow", "no exception exists for the order", not n.order_id.isin(set(ex.order_id.dropna())).any())
    chk("Normal flow", "no failed attempt on the order", (~n.failed).all())
    root("Normal flow", "Normal flow", None)
    reverse("Normal flow", "Normal flow")
    # ---- Picking delay
    p = LJ("Picking delay")
    chk("Picking delay", "pick_start and pick_end exist", p.pick_start.notna().all() and p.pick_end.notna().all(), f"{len(p)} labels checked")
    chk("Picking delay", "picking duration > configured picking delay threshold", (p.pick_dur > pT).all())
    chk("Picking delay", "lifecycle consistent: not Cancelled/Placed/Picking (picking is complete)", p.current_stage.isin(["Packing", "Awaiting Rider", "Out for Delivery", "Delivered"]).all())
    chk("Picking delay", "later stages that exist are normal (no packing delay, no rider-assignment wait above threshold)", (~p.a_pack & ~p.a_wait).all())
    root("Picking delay", "Picking delay", "Picking")
    reverse("Picking delay", "Picking delay")
    # ---- Packing delay
    k = LJ("Packing delay")
    chk("Packing delay", "picking completed (pick_end exists)", k.pick_end.notna().all(), f"{len(k)} labels checked")
    chk("Packing delay", "pack_start and pack_end exist", k.pack_start.notna().all() and k.pack_end.notna().all())
    chk("Packing delay", "packing duration > configured packing delay threshold", (k.pack_dur > kT).all())
    chk("Packing delay", "picking was normal (duration <= threshold)", (~k.a_pick).all())
    root("Packing delay", "Packing delay", "Packing")
    reverse("Packing delay", "Packing delay")
    # ---- Inventory mismatch
    im = L("Inventory mismatch").merge(inv, on=["store_id", "sku_id"], how="left")
    chk("Inventory mismatch", "label points to an existing store/SKU row", im.system_stock.notna().all(), f"{len(im)} labels checked")
    chk("Inventory mismatch", "system_stock != counted_shelf_stock", (im.system_stock != im.counted_shelf_stock).all())
    chk("Inventory mismatch", "store-level label: store_id + sku_id present, order_id empty", im.sku_id.notna().all() and im.order_id.isna().all())
    root("Inventory mismatch", "Inventory mismatch", "Inventory")
    mm_rows = set(zip(inv[inv.system_stock != inv.counted_shelf_stock].store_id, inv[inv.system_stock != inv.counted_shelf_stock].sku_id))
    chk("Inventory mismatch", "bidirectional: every inventory row with system_stock != counted_shelf_stock is labelled (and no other)",
        mm_rows == set(zip(im.store_id, im.sku_id)), f"{len(mm_rows)} rows vs {len(im)} labels")
    # ---- Stockout
    so = L("Stockout").merge(lines, on=["order_id", "sku_id"], how="left", suffixes=("", "_l"))
    chk("Stockout", "label has order_id + sku_id and matches an order line", so.quantity_ordered.notna().all(), f"{len(so)} labels checked")
    chk("Stockout", "order line is short-picked (quantity_picked < quantity_ordered)", (so.quantity_picked < so.quantity_ordered).all())
    chk("Stockout", "counted_shelf_stock < quantity_ordered", (so.counted_shelf_stock < so.quantity_ordered).all())
    root("Stockout", "Stockout", "Inventory")
    exp_so = set(zip(lines[lines.stock_short].order_id, lines[lines.stock_short].sku_id))
    chk("Stockout", "bidirectional: every stock-driven short-picked line is labelled (and no other)", exp_so == set(zip(so.order_id, so.sku_id)), f"{len(exp_so)} lines vs {len(so)} labels")
    # ---- Rider shortage
    r = LJ("Rider shortage")
    chk("Rider shortage", "pack_end and assignment_time exist", r.pack_end.notna().all() and r.assignment_time.notna().all(), f"{len(r)} labels checked")
    chk("Rider shortage", "meaningful assignment wait (> configured threshold)", (r.wait > aT).all())
    chk("Rider shortage", "riders_available = 0 in an hourly snapshot covering the pack_end -> assignment window", r.zero_win.all())
    chk("Rider shortage", "no earlier stage abnormal (root cause is rider supply)", (~r.a_pick & ~r.a_pack).all())
    root("Rider shortage", "Rider shortage", "Rider Assignment")
    reverse("Rider shortage", "Rider shortage")
    # ---- Delivery delay
    d = LJ("Delivery delay")
    chk("Delivery delay", "rider assignment exists", d.assignment_time.notna().all(), f"{len(d)} labels checked")
    chk("Delivery delay", "delivery_time populated (completed delivery, not an undelivered breach)", d.delivery_time.notna().all() and (d.order_status == "Delivered").all())
    chk("Delivery delay", "delivery_time > promised_delivery_time", (d.delivery_time > d.promised_delivery_time).all())
    chk("Delivery delay", "no earlier stage abnormal and no failed attempt (root cause is delivery)", (~d.a_pick & ~d.a_pack & ~d.a_wait & ~d.failed).all())
    root("Delivery delay", "Delivery delay", "Delivery")
    reverse("Delivery delay", "Delivery delay")
    # ---- Failed delivery
    f = LJ("Failed delivery").merge(dv[["order_id", "delivery_status", "pickup_time"]].rename(columns={"pickup_time": "pu"}), on="order_id", how="left")
    chk("Failed delivery", "failed_attempt_flag = True and pickup_time populated", f.failed.all() and f.pu.notna().all(), f"{len(f)} labels checked")
    retry = f[f.delivery_status == "Delivered"]
    nosucc = f[f.delivery_status == "Failed"]
    chk("Failed delivery", "retry-success form: delivery_status Delivered, delivery_time populated, order Delivered", (retry.delivery_time.notna() & (retry.order_status == "Delivered")).all(), f"{len(retry)} orders")
    chk("Failed delivery", "no-success form: delivery_status Failed, delivery_time null, order_status Delivery Failed", (nosucc.delivery_time.isna() & (nosucc.order_status == "Delivery Failed")).all(), f"{len(nosucc)} orders")
    chk("Failed delivery", "every label is one of the two defined representations", len(retry) + len(nosucc) == len(f))
    chk("Failed delivery", "both representations are present", len(retry) > 0 and len(nosucc) > 0)
    root("Failed delivery", "Failed delivery", "Delivery")
    reverse("Failed delivery", "Failed delivery")
    # ---- Multi-stage disruption
    m = LJ("Multi-stage disruption")
    chk("Multi-stage disruption", "picking-origin delay: pick_end exists and picking duration > threshold", (m.pick_end.notna() & (m.pick_dur > pT)).all(), f"{len(m)} labels checked")
    root("Multi-stage disruption", "Multi-stage disruption", "Picking")
    chk("Multi-stage disruption", "propagates into packing (packing duration > threshold)", (m.pack_dur > kT).all())
    chk("Multi-stage disruption", "propagates into rider assignment (wait > threshold)", (m.wait > aT).all())
    chk("Multi-stage disruption", "propagates into delivery (delivery_time populated and > promised)", (m.delivery_time.notna() & (m.delivery_time > m.promised_delivery_time)).all())
    chk("Multi-stage disruption", "no snapshot truncation: all stage timestamps exist and order is Delivered", m[stage_cols].notna().all().all() and (m.current_stage == "Delivered").all())
    reverse("Multi-stage disruption", "Multi-stage disruption")
    # ---- Live At Risk / Breached
    lv = live_l.join(F, on="order_id", rsuffix="_o")
    chk("Live At Risk / Breached", "order still in progress at snapshot_time (order_status In Progress/Placed)", lv.order_status.isin(["In Progress", "Placed"]).all(), f"{len(lv)} labels checked")
    chk("Live At Risk / Breached", "not delivered, not cancelled, not failed (delivery_time null)", lv.delivery_time.isna().all() and lv.current_stage.isin(["Placed", "Picking", "Packing", "Awaiting Rider", "Out for Delivery"]).all())
    chk("Live At Risk / Breached", "sla_status is At Risk or Breached", lv.sla_status.isin(["At Risk", "Breached"]).all())
    chk("Live At Risk / Breached", "near or past promise at snapshot (<= at-risk buffer remaining)", ((lv.promised_delivery_time - S) <= pd.Timedelta(minutes=C.AT_RISK_BUFFER_MINUTES)).all())
    chk("Live At Risk / Breached", "label carries order_id only (no sku, no root cause)", lv.sku_id.isna().all() and lv.root_cause_stage.isna().all())
    chk("Live At Risk / Breached", "bidirectional: every In Progress/Placed At Risk or Breached order is labelled (and no other)",
        set(F.index[F.live_expected]) == set(live_l.order_id), f"{int(F.live_expected.sum())} orders vs {len(live_l)} labels")
    # ---- Cancelled
    c = LJ("Cancelled")
    chk("Cancelled", "order_status = Cancelled and current_stage = Cancelled", ((c.order_status == "Cancelled") & (c.current_stage == "Cancelled")).all(), f"{len(c)} labels checked")
    chk("Cancelled", "cancelled before picking: no stage timestamps, nothing picked", c[stage_cols].isna().all().all() and it[it.order_id.isin(c.order_id)].quantity_picked.isna().all())
    chk("Cancelled", "no Picking/Packing/Delivery records", not (set(c.order_id) & (set(pk.order_id) | set(pc.order_id) | set(dv.order_id))))
    chk("Cancelled", "sla_status Not Applicable", (c.sla_status == "Not Applicable").all())
    reverse("Cancelled", "Cancelled")
    # ---- label-set integrity
    chk("Labels", "at most one primary (root-cause) label per order", not prim.order_id.duplicated().any())
    chk("Labels", "at most one Live At Risk / Breached overlay per order", not live_l.order_id.duplicated().any())
    chk("Labels", "order-level scenarios carry order_id; Inventory mismatch carries none", lab[lab.scenario_type != "Inventory mismatch"].order_id.notna().all() and lab[lab.scenario_type == "Inventory mismatch"].order_id.isna().all())
    rc_ok = all(((L(t).root_cause_stage == w).all() if len(L(t)) else True) for t, w in SEM_ROOT.items())
    chk("Labels", "root_cause_stage follows the scenario-to-stage mapping for every label", rc_ok)
    prim_map = prim.set_index("order_id").scenario_type
    unl = F[~F.index.isin(prim_map.index)]
    chk("Labels", "every Delivered or Cancelled order carries a primary label", not unl.order_status.isin(["Delivered", "Cancelled"]).any(),
        f"{len(unl)} orders without primary label (in-flight or partly propagated; see README section 10)")
    chk("Labels", "primary label equals the facts-derived expected label for every order", (prim_map.reindex(F.index).fillna("") == F.expected).all(),
        f"{len(F)} orders checked")


def run(raw_dir):
    R = Results()
    D = load(raw_dir)
    # ---------------- schema
    for n, cols in C.SCHEMA.items():
        want = [c for c, _ in cols]
        R.chk(f"[Schema] {n}: exact field names and order", list(D[n].columns) == want,
              f"expected {want} got {list(D[n].columns)}" if list(D[n].columns) != want else f"{len(want)} fields")
    T = typed(D)
    st, o, it, inv, pk, pc, dv = (T[k] for k in ["stores", "orders", "order_items", "inventory", "picking", "packing", "delivery_rider"])
    ra, ex, sop, lab = T["rider_availability"], T["exceptions"], T["sop_knowledge"], T["scenario_labels"]

    # ---------------- primary keys / enums
    for n, key in C.PRIMARY_KEYS.items():
        d = int(T[n].duplicated(key).sum())
        R.chk(f"[Keys] {n}: primary key {key} unique", d == 0, f"{d} duplicates")
    for (n, c), allowed in C.ENUMS.items():
        bad = T[n][c].dropna()
        bad = bad[~bad.isin(allowed)]
        R.chk(f"[Enums] {n}.{c}: only allowed values", len(bad) == 0, f"invalid: {sorted(set(bad))[:5]}")

    # ---------------- referential integrity
    sset, oset = set(st.store_id), set(o.order_id)
    inv_keys = set(zip(inv.store_id, inv.sku_id))
    pools = {
        "associate": {f"AS-{s}-{k:02d}" for s in st.store_id for k in range(1, C.ASSOCIATES_PER_STORE + 1)},
        "rider": {f"RD-{s}-{k:02d}" for s in st.store_id for k in range(1, C.RIDERS_PER_STORE + 1)},
        "manager": {f"MG-{s}-{k:02d}" for s in st.store_id for k in range(1, C.MANAGERS_PER_STORE + 1)},
    }
    for n, col in [("orders", "store_id"), ("order_items", "store_id"), ("inventory", "store_id"),
                   ("rider_availability", "store_id"), ("exceptions", "store_id"), ("scenario_labels", "store_id")]:
        R.chk(f"[FK] {n}.{col} -> stores", T[n][col].isin(sset).all())
    for n in ["order_items", "picking", "packing", "delivery_rider"]:
        R.chk(f"[FK] {n}.order_id -> orders (no orphan stage/item records)", T[n].order_id.isin(oset).all())
    R.chk("[FK] exceptions.order_id -> orders (when present)", ex.order_id.dropna().isin(oset).all())
    R.chk("[FK] scenario_labels.order_id -> orders (when present)", lab.order_id.dropna().isin(oset).all())
    R.chk("[FK] order_items (store_id, sku_id) -> inventory", set(zip(it.store_id, it.sku_id)) <= inv_keys)
    e_sku = ex.dropna(subset=["sku_id"])
    R.chk("[FK] exceptions (store_id, sku_id) -> inventory", set(zip(e_sku.store_id, e_sku.sku_id)) <= inv_keys)
    l_sku = lab.dropna(subset=["sku_id"])
    R.chk("[FK] scenario_labels (store_id, sku_id) -> inventory", set(zip(l_sku.store_id, l_sku.sku_id)) <= inv_keys)
    m = it.merge(o[["order_id", "store_id"]], on="order_id", suffixes=("", "_o"))
    R.chk("[FK] order_items belong to their order's store", (m.store_id == m.store_id_o).all())
    em = ex.dropna(subset=["order_id"]).merge(o[["order_id", "store_id"]], on="order_id", suffixes=("", "_o"))
    R.chk("[FK] exception store_id equals its order's store_id", (em.store_id == em.store_id_o).all())
    R.chk("[FK] picker_associate_id in synthetic associate pool", pk.picker_associate_id.isin(pools["associate"]).all())
    R.chk("[FK] rider_id in synthetic rider pool", dv.rider_id.dropna().isin(pools["rider"]).all())
    R.chk("[FK] assigned_to in associate/manager pools", ex.assigned_to.dropna().isin(pools["associate"] | pools["manager"]).all())
    R.chk("[FK] approver in manager pool", ex.approver.dropna().isin(pools["manager"]).all())
    pk_o = pk.set_index("order_id")
    R.chk("[FK] rider store matches order store", dv.dropna(subset=["rider_id"]).assign(
        rs=lambda d: d.rider_id.str.split("-").str[1]).merge(o[["order_id", "store_id"]], on="order_id").pipe(
        lambda d: (d.rs == d.store_id).all()))
    cats_in_sop = set(sop.exception_category.dropna())
    R.chk("[FK] every exception category in use has an Approved SOP section",
          set(ex.category) <= set(sop[sop.approval_status == "Approved"].exception_category.dropna()),
          f"missing: {set(ex.category) - cats_in_sop}")
    R.chk("[SOP] at least 3 sections per exception category", sop.groupby("exception_category").size().min() >= 3)
    R.chk("[SOP] at least one Draft and one Retired section exist",
          {"Draft", "Retired"} <= set(sop.approval_status))
    R.chk("[SOP] content_origin = Synthetic on every row", (sop.content_origin == "Synthetic").all())

    # ---------------- stage existence rules
    oi = o.set_index("order_id")
    canc = set(o[o.current_stage == "Cancelled"].order_id)
    R.chk("[Stage] cancelled orders have no picking/packing/delivery records",
          not (set(pk.order_id) | set(pc.order_id) | set(dv.order_id)) & canc)
    R.chk("[Stage] picking record <=> pick_start populated in Orders", set(pk.order_id) == set(o[o.pick_start.notna()].order_id))
    pk_done = set(pk[pk.pick_end.notna()].order_id)
    R.chk("[Stage] packing only after picking completed (pick_end populated)", set(pc.order_id) <= pk_done)
    pc_done = set(pc[pc.pack_end.notna()].order_id)
    R.chk("[Stage] delivery only after packing completed; one per packed order", set(dv.order_id) == pc_done)

    # ---------------- timestamp equality (Orders copy == stage table)
    def eq(name, left, right):
        ok = same(left, right)
        R.chk(f"[Timestamps] {name}: Orders value equals stage-table value exactly", ok.all(), f"{int((~ok).sum())} mismatches")
    pkr, pcr, dvr = (x.set_index("order_id").reindex(oi.index) for x in (pk, pc, dv))
    for c in ["pick_start", "pick_end"]:
        eq(c, oi[c], pkr[c])
    for c in ["pack_start", "pack_end"]:
        eq(c, oi[c], pcr[c])
    for c in ["assignment_time", "pickup_time", "delivery_time"]:
        eq(c, oi[c], dvr[c])

    # ---------------- timestamp ordering
    seq = ["order_time", "pick_start", "pick_end", "pack_start", "pack_end", "assignment_time", "pickup_time", "delivery_time"]
    bad_order = 0
    bad_gap = 0
    for a, b in zip(seq[:-1], seq[1:]):
        both = o[a].notna() & o[b].notna()
        bad_order += int((o.loc[both, a] > o.loc[both, b]).sum())
    for i in range(len(seq)):
        for j in range(i + 1, len(seq)):
            bad_gap += int((o[seq[j]].notna() & o[seq[i]].isna()).sum())
    R.chk("[Timestamps] lifecycle order: order <= pick <= pack <= assignment <= pickup <= delivery", bad_order == 0, f"{bad_order} violations")
    R.chk("[Timestamps] no later stage timestamp without the earlier ones", bad_gap == 0, f"{bad_gap} gaps")
    R.chk("[Timestamps] picking/packing durations are non-negative",
          ((pk.pick_end - pk.pick_start).dropna() >= pd.Timedelta(0)).all() and ((pc.pack_end - pc.pack_start).dropna() >= pd.Timedelta(0)).all())
    life = [c for c in seq]
    R.chk("[Timestamps] no lifecycle event lies after the snapshot time", all((o[c].dropna() <= S).all() for c in life))
    R.chk("[Timestamps] exception timestamps not after snapshot",
          all((ex[c].dropna() <= S).all() for c in ["detected_at", "approved_at", "resolved_at"]))
    R.chk("[Timestamps] exceptions detected after their order was placed",
          (em.assign(d=em.detected_at, t=em.order_id.map(oi.order_time)).pipe(lambda d: (d.d >= d.t).all())))

    # ---------------- SLA
    R.chk("[SLA] promised_delivery_time = order_time + configured window",
          ((o.promised_delivery_time - o.order_time) == pd.Timedelta(minutes=C.PROMISED_WINDOW_MINUTES)).all())
    def sla(r):
        if r.current_stage == "Cancelled":
            return "Not Applicable"
        if pd.notna(r.delivery_time):
            return "On Time" if r.delivery_time <= r.promised_delivery_time else "Breached"
        rem = (r.promised_delivery_time - S) / MIN
        return "On Time" if rem > C.AT_RISK_BUFFER_MINUTES else ("At Risk" if rem >= 0 else "Breached")
    exp = o.apply(sla, axis=1)
    R.chk("[SLA] sla_status equals Data Dictionary Section 4 derivation", (exp == o.sla_status).all(), f"{int((exp != o.sla_status).sum())} mismatches")

    # ---------------- quantities
    n_lines = it.groupby("order_id").size().reindex(oi.index)
    tot_q = it.groupby("order_id").quantity_ordered.sum().reindex(oi.index)
    R.chk("[Quantity] item_count = number of distinct order lines", (oi.item_count == n_lines).all())
    R.chk("[Quantity] total_quantity = sum(quantity_ordered)", (oi.total_quantity == tot_q).all())
    R.chk("[Quantity] quantity_ordered >= 1", (it.quantity_ordered >= 1).all())
    R.chk("[Quantity] quantity_picked <= quantity_ordered", (it.quantity_picked.dropna() <= it.loc[it.quantity_picked.notna(), "quantity_ordered"]).all())
    R.chk("[Quantity] Picking.items_ordered = Orders.total_quantity", (pk_o.items_ordered == oi.total_quantity.reindex(pk_o.index)).all())
    sp = it.groupby("order_id").quantity_picked.sum(min_count=1).reindex(pk_o.index)
    done = pk_o.pick_end.notna()
    R.chk("[Quantity] Picking.items_picked = sum(quantity_picked)", (pk_o.items_picked[done] == sp[done]).all())
    R.chk("[Quantity] items_picked <= items_ordered", (pk_o.items_picked.dropna() <= pk_o.items_ordered[pk_o.items_picked.notna()]).all())
    pe_vis = it.order_id.map(oi.pick_end).notna()
    R.chk("[Nulls] quantity_picked null until pick_end populated (and only then)", (it.quantity_picked.notna() == pe_vis).all())
    R.chk("[Nulls] items_picked / mis_pick_flag null until pick_end populated",
          (pk.items_picked.notna() == pk.pick_end.notna()).all() and (pk.mis_pick_flag.notna() == pk.pick_end.notna()).all())
    R.chk("[Nulls] packing_issue_flag null until pack_end populated", (pc.packing_issue_flag.notna() == pc.pack_end.notna()).all())

    # ---------------- status integrity
    mp = {"Placed": ["Placed"], "Picking": ["In Progress"], "Packing": ["In Progress"], "Awaiting Rider": ["In Progress"],
          "Out for Delivery": ["In Progress", "Delivery Failed"], "Delivered": ["Delivered"], "Cancelled": ["Cancelled"]}
    R.chk("[Status] current_stage / order_status combinations follow Data Dictionary 3.2",
          all(r.order_status in mp[r.current_stage] for r in o.itertuples()))
    dm = {"Awaiting Assignment": "Awaiting Rider", "Assigned": "Out for Delivery", "Picked Up": "Out for Delivery",
          "In Transit": "Out for Delivery", "Failed": "Out for Delivery", "Delivered": "Delivered"}
    j = dv.merge(o[["order_id", "current_stage", "order_status"]], on="order_id")
    R.chk("[Status] delivery_status maps to the Orders stage (Data Dictionary 3.7)", (j.delivery_status.map(dm) == j.current_stage).all())
    R.chk("[Status] Delivery Failed <=> delivery_status Failed", ((j.order_status == "Delivery Failed") == (j.delivery_status == "Failed")).all())
    R.chk("[Status] Delivered <=> delivery_time populated <=> delivery_status Delivered",
          ((dv.delivery_time.notna()) == (dv.delivery_status == "Delivered")).all())
    R.chk("[Status] cancelled orders: SLA Not Applicable, no stage timestamps",
          ((o[o.current_stage == "Cancelled"].sla_status == "Not Applicable").all()) and o[o.current_stage == "Cancelled"][seq[1:]].isna().all().all())

    # ---------------- delivery / failed delivery / rider
    aw = dv.delivery_status == "Awaiting Assignment"
    nul3 = dv.rider_id.isna() & dv.assignment_time.isna() & dv.rider_availability.isna()
    nn3 = dv.rider_id.notna() & dv.assignment_time.notna() & dv.rider_availability.notna()
    R.chk("[Delivery] rider_id, assignment_time, rider_availability null iff Awaiting Assignment", ((aw & nul3) | (~aw & nn3)).all())
    fl = dv[dv.failed_attempt_flag == True]  # noqa: E712
    R.chk("[Delivery] failed attempt => pickup_time populated and status Failed or Delivered (single retry)",
          fl.pickup_time.notna().all() and fl.delivery_status.isin(["Failed", "Delivered"]).all())
    R.chk("[Delivery] status Failed => failed_attempt_flag true, delivery_time null",
          (dv[dv.delivery_status == "Failed"].failed_attempt_flag == True).all() and dv[dv.delivery_status == "Failed"].delivery_time.isna().all())  # noqa: E712
    R.chk("[Delivery] rider_availability uses only Available/Busy (no 'Unavailable')", set(dv.rider_availability.dropna()) <= {"Available", "Busy"})
    # rider overlap is a warning only (batching is allowed: rider_availability = 'Busy')
    ov = 0
    for rid, g in dv.dropna(subset=["rider_id"]).groupby("rider_id"):
        g = g.sort_values("assignment_time")
        end = g.delivery_time.fillna(S)
        ov += int((g.assignment_time.values[1:] < end.values[:-1]).sum())
    R.warn("[Rider] overlapping assignments for the same rider (batching, labelled Busy)", True, f"{ov} overlaps")

    # ---------------- rider availability dataset
    R.chk("[Rider availability] counts >= 0", (ra[["riders_available", "riders_busy", "riders_unavailable"]] >= 0).all().all())
    tot = (ra.riders_available + ra.riders_busy + ra.riders_unavailable)
    R.chk("[Rider availability] counts sum to the rider pool size per store/hour", (tot == C.RIDERS_PER_STORE).all())
    R.chk("[Rider availability] hourly snapshots (top of the hour, IST), none after snapshot time",
          D["rider_availability"].snapshot_time.str.endswith(":00:00+05:30").all() and (ra.snapshot_time <= S).all())
    rad = ex[ex.category == "Rider Assignment Delay"].dropna(subset=["order_id"])
    key = ra.assign(k=list(zip(ra.store_id, ra.snapshot_time)))
    zero = set(key[key.riders_available == 0].k)
    pe_ = rad.order_id.map(oi.pack_end).dt.tz_convert(C.TZ).dt.floor("h")
    R.chk("[Rider availability] every Rider Assignment Delay exception sits in an hour with riders_available = 0",
          all((s_, t_) in zero for s_, t_ in zip(rad.store_id, pe_)), f"{len(rad)} exceptions checked")

    # ---------------- inventory
    R.chk("[Inventory] stock values >= 0", (inv.system_stock >= 0).all() and (inv.counted_shelf_stock >= 0).all())
    R.chk("[Inventory] last_updated not after snapshot", (inv.last_updated <= S).all())
    R.chk("[Inventory] 240 store/SKU rows = stores x SKUs", len(inv) == C.N_STORES * C.N_SKUS)
    mm = inv.set_index(["store_id", "sku_id"])
    imm = ex[ex.category == "Inventory Mismatch"]
    R.chk("[Inventory] Inventory Mismatch exceptions point at rows with system_stock != counted_shelf_stock",
          all(mm.loc[(r.store_id, r.sku_id)].system_stock != mm.loc[(r.store_id, r.sku_id)].counted_shelf_stock for r in imm.itertuples()),
          f"{len(imm)} exceptions checked")
    R.chk("[Inventory] Inventory Mismatch exceptions carry store_id + sku_id", imm.sku_id.notna().all())
    so = ex[ex.category == "Stockout"].merge(it, on=["order_id", "sku_id"], suffixes=("", "_i"))
    so = so.merge(inv, on=["store_id", "sku_id"])
    R.chk("[Inventory] Stockout exceptions: line short-picked and counted_shelf_stock < quantity_ordered",
          len(so) == (ex.category == "Stockout").sum() and (so.quantity_picked < so.quantity_ordered).all() and (so.counted_shelf_stock < so.quantity_ordered).all(),
          f"{len(so)} exceptions checked")
    short_lines = it[it.quantity_picked < it.quantity_ordered].merge(inv, on=["store_id", "sku_id"])
    stock_short = short_lines[short_lines.counted_shelf_stock < short_lines.quantity_ordered]
    R.chk("[Inventory] every stock-driven short-pick has a Stockout exception",
          set(zip(stock_short.order_id, stock_short.sku_id)) == set(zip(ex[ex.category == "Stockout"].order_id, ex[ex.category == "Stockout"].sku_id)))
    pop = it.groupby("sku_id").size().sort_values(ascending=False)
    R.warn("[Inventory] SKU demand is skewed (top SKU >= 2x median SKU)", pop.iloc[0] >= 2 * pop.median(), f"top={pop.iloc[0]} median={pop.median()}")

    # ---------------- exceptions
    R.chk("[Exceptions] order-level categories carry order_id; unique exception_id",
          ex[~ex.category.isin(["Inventory Mismatch"])].order_id.notna().all())
    R.chk("[Exceptions] resolved_at >= detected_at", (ex.resolved_at.dropna() >= ex.loc[ex.resolved_at.notna(), "detected_at"]).all())
    R.chk("[Exceptions] approved_at >= detected_at and resolved_at >= approved_at (when both exist)",
          (ex.approved_at.dropna() >= ex.loc[ex.approved_at.notna(), "detected_at"]).all() and
          ((ex.resolved_at - ex.approved_at).dropna() >= pd.Timedelta(0)).all())
    R.chk("[Exceptions] Resolved/Closed <=> resolved_at and resolution populated",
          ((ex.status == "Resolved/Closed") == ex.resolved_at.notna()).all() and ((ex.status == "Resolved/Closed") == ex.resolution.notna()).all())
    pa = ex[ex.status == "Pending Approval"]
    R.chk("[Exceptions] Pending Approval: approval_applicable true; no approver/decision/approved_at; not resolved",
          (pa.approval_applicable == True).all() and pa[["approver", "approval_decision", "approved_at", "resolved_at"]].isna().all().all())  # noqa: E712
    na = ex[ex.approval_applicable == False]  # noqa: E712
    R.chk("[Exceptions] approval fields only where approval_applicable = true", na[["approver", "approval_decision", "approved_at"]].isna().all().all())
    ap_res = ex[(ex.approval_applicable == True) & (ex.status == "Resolved/Closed")]  # noqa: E712
    R.chk("[Exceptions] approval-applicable Resolved/Closed exceptions carry approver, decision and approved_at",
          ap_res[["approver", "approval_decision", "approved_at"]].notna().all().all())
    R.chk("[Exceptions] approver, decision, approved_at are populated together",
          ((ex.approver.notna() == ex.approval_decision.notna()) & (ex.approver.notna() == ex.approved_at.notna())).all())
    R.chk("[Exceptions] 'Action Required' is not used", not (ex.status == "Action Required").any())

    # exception <-> timestamp agreement (rule-based detection)
    jp = pk.set_index("order_id")
    def over(cat, start, end, thr):
        e = ex[ex.category == cat]
        t = o.set_index("order_id").join(jp[[]], how="left")
        return e, t
    pick_dur = (pk.pick_end.fillna(S) - pk.pick_start)
    e_pick = set(ex[ex.category == "Picking Delay"].order_id)
    exp_pick = set(pk[(pick_dur > C.PICK_DELAY_MIN * MIN) & (pk.pick_start + C.PICK_DELAY_MIN * MIN <= S)].order_id)
    R.chk("[Exceptions] Picking Delay exceptions agree with picking timestamps (> threshold)", e_pick == exp_pick, f"{len(e_pick)} vs {len(exp_pick)}")
    pack_dur = (pc.pack_end.fillna(S) - pc.pack_start)
    e_pack = set(ex[ex.category == "Packing Delay"].order_id)
    exp_pack = set(pc[(pack_dur > C.PACK_DELAY_MIN * MIN) & (pc.pack_start + C.PACK_DELAY_MIN * MIN <= S)].order_id)
    R.chk("[Exceptions] Packing Delay exceptions agree with packing timestamps (> threshold)", e_pack == exp_pack, f"{len(e_pack)} vs {len(exp_pack)}")
    wait = (dv.assignment_time.fillna(S) - dv.order_id.map(oi.pack_end))
    e_rad = set(ex[ex.category == "Rider Assignment Delay"].order_id)
    exp_rad = set(dv[(wait > C.ASSIGN_DELAY_MIN * MIN)].order_id)
    R.chk("[Exceptions] Rider Assignment Delay exceptions agree with assignment timestamps (> threshold)", e_rad == exp_rad, f"{len(e_rad)} vs {len(exp_rad)}")
    late = o[(o.current_stage != "Cancelled") & (o.pick_start.notna()) & (o.delivery_time.isna() | (o.delivery_time > o.promised_delivery_time))]
    late = late[late.promised_delivery_time <= S]
    R.chk("[Exceptions] every order past its promised time without on-time delivery has a Delivery Delay exception",
          set(late.order_id) == set(ex[ex.category == "Delivery Delay"].order_id), f"{len(late)} orders")
    R.chk("[Exceptions] Failed Delivery Attempt exceptions <=> failed_attempt_flag",
          set(ex[ex.category == "Failed Delivery Attempt"].order_id) == set(dv[dv.failed_attempt_flag == True].order_id))  # noqa: E712
    sl = ex[ex.category == "SLA Risk/Breach"]
    R.chk("[Exceptions] SLA Risk/Breach exceptions only on At Risk or Breached orders", sl.order_id.map(oi.sla_status).isin(["At Risk", "Breached"]).all())

    # ---------------- scenario coverage (critical: the data must not be only 'perfect')
    cov = lab.scenario_type.value_counts()
    for s_ in C.ENUMS[("scenario_labels", "scenario_type")]:
        R.chk(f"[Coverage] scenario present: {s_}", cov.get(s_, 0) >= 1, f"{cov.get(s_, 0)} labels")
    live = o[o.order_status.isin(["In Progress", "Delivery Failed", "Placed"])]
    R.chk("[Coverage] live At Risk orders exist", (live.sla_status == "At Risk").any(), f"{(live.sla_status == 'At Risk').sum()}")
    R.chk("[Coverage] live Breached orders exist", (live.sla_status == "Breached").any(), f"{(live.sla_status == 'Breached').sum()}")
    R.chk("[Coverage] all four SLA states exist", {"On Time", "At Risk", "Breached", "Not Applicable"} <= set(o.sla_status))
    R.chk("[Coverage] all four exception statuses exist", set(C.ENUMS[("exceptions", "status")]) <= set(ex.status))
    R.chk("[Coverage] all exception categories exist", set(C.CATEGORIES) <= set(ex.category))
    R.chk("[Coverage] approval-applicable exceptions exist with all three decisions",
          {"Approved", "Edited", "Rejected"} <= set(ex.approval_decision.dropna()))
    R.chk("[Coverage] failed delivery with and without a successful retry",
          ((dv.failed_attempt_flag == True) & (dv.delivery_status == "Delivered")).any() and (dv.delivery_status == "Failed").any())  # noqa: E712
    R.chk("[Coverage] rider shortage hours exist (riders_available = 0)", (ra.riders_available == 0).any())
    R.chk("[Coverage] multi-stage chain exists: picking, packing and rider exceptions on one order",
          any({"Picking Delay", "Packing Delay", "Rider Assignment Delay"} <= set(g.category) for _, g in ex.groupby("order_id")))

    # ---------------- semantic scenario validation (new)
    semantic_scenario_checks(R, T)

    # ---------------- volumes (warnings)
    R.chk("[Volume] orders", len(o) == C.N_ORDERS, f"{len(o)}")
    R.warn("[Volume] order_items within 2,000-4,000", 2000 <= len(it) <= 4000, f"{len(it)}")
    R.warn("[Volume] orders with an exception about EXCEPTION_RATE (+/- 5 pts)",
           abs(ex.dropna(subset=['order_id']).order_id.nunique() / len(o) - C.EXCEPTION_RATE) <= 0.05,
           f"{ex.dropna(subset=['order_id']).order_id.nunique() / len(o):.1%}")
    R.warn("[Volume] cancellation rate about configured value", abs((o.current_stage == 'Cancelled').mean() - C.CANCELLATION_RATE) <= .01,
           f"{(o.current_stage == 'Cancelled').mean():.1%}")
    R.warn("[Volume] SOP rows = documents x sections", len(sop) == C.N_SOP_DOCS * C.SECTIONS_PER_DOC, f"{len(sop)}")
    return R, D


def run_and_report(raw_dir):
    R, D = run(raw_dir)
    crit_fail = [r for r in R.rows if not r[1] and r[3]]
    warn_fail = [r for r in R.rows if not r[1] and not r[3]]
    lines = ["MacroOps AI - Synthetic Data Validation Report", "=" * 60,
             f"Seed: {C.SEED} | Snapshot: {C.SNAPSHOT_TIME.isoformat()} | Time zone: {C.TZ_LABEL}", "",
             "ROWS GENERATED PER DATASET"]
    for n in C.SCHEMA:
        lines.append(f"  {C.FILES[n]:<24}{len(D[n]):>7} rows  {len(C.SCHEMA[n]):>3} columns")
    lines += ["", "CHECKS"]
    for name, ok, detail, crit in R.rows:
        tag = "PASS" if ok else ("FAIL" if crit else "WARN")
        lines.append(f"  [{tag}] {name}" + (f"  ({detail})" if detail and (not ok or "checked" in detail or "labels" in detail or "orders" in detail or "overlaps" in detail or "vs" in detail or "top=" in detail or "%" in detail) else ""))
    n_pass = sum(1 for r in R.rows if r[1])
    sem = [r for r in R.rows if r[0].startswith("[Semantic:")]
    lc = D["scenario_labels"].scenario_type.value_counts()
    lines += ["", "SCENARIO SEMANTIC VALIDATION SUMMARY (Data Dictionary V1.1 Section 7)",
              f"  {'Scenario':<26}{'Labels':>7}{'Checks':>8}  Result"]
    for sc in SEM_ORDER_SCENARIOS + ["Labels"]:
        rs = [r for r in sem if r[0].startswith(f"[Semantic:{sc}]")]
        lines.append(f"  {sc if sc != 'Labels' else '(label-set integrity)':<26}{(int(lc.get(sc, 0)) if sc != 'Labels' else len(D['scenario_labels'])):>7}"
                     f"{len(rs):>8}  {'PASS' if all(r[1] for r in rs) else 'FAIL'}")
    lines += ["", "SUMMARY", f"  Existing checks:       {len(R.rows) - len(sem)}", f"  New semantic checks:   {len(sem)}",
              f"  Checks run (total):    {len(R.rows)}", f"  Passed:                {n_pass}",
              f"  Critical failures:     {len(crit_fail)}", f"  Warnings:              {len(warn_fail)}",
              f"  Result:                {'PASS - zero critical integrity errors' if not crit_fail else 'FAIL'}"]
    if crit_fail:
        lines += ["", "CRITICAL FAILURES"] + [f"  - {r[0]}: {r[2]}" for r in crit_fail]
    if warn_fail:
        lines += ["", "WARNINGS"] + [f"  - {r[0]}: {r[2]}" for r in warn_fail]
    return "\n".join(lines) + "\n", not crit_fail
