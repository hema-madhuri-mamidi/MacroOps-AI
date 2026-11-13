#!/usr/bin/env python3
"""
MacroOps AI - reproducible synthetic dataset generator.

Pipeline:  Data Dictionary V1.1  ->  this generator  ->  CSVs  ->  validation  ->  report

Run:       python generator/generate_data.py            (from the project root)

All data is synthetic and non-production. All parameters live in config.py.
The generator uses a single numpy Generator seeded with config.SEED, and every draw happens in
a fixed order, so the same config + seed always produces byte-identical CSV files.
"""
import hashlib
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C                      # noqa: E402
import validators as V                  # noqa: E402
from sop_content import DOCS            # noqa: E402

S = C.SNAPSHOT_TIME


def mins(m):
    return timedelta(seconds=int(round(m * 60)))


def secs(s):
    return timedelta(seconds=int(round(s)))


def iso(d):
    return "" if d is None or (isinstance(d, float) and np.isnan(d)) else d.isoformat(timespec="seconds")


def vis(t):
    """A lifecycle event is visible only if it has already happened at the snapshot time."""
    return t if (t is not None and t <= S) else None


def pick(rng, seq):
    return seq[int(rng.integers(0, len(seq)))]


ROOT_CAUSE = {"Stockout": "Inventory", "Picking delay": "Picking", "Multi-stage disruption": "Picking",
              "Packing delay": "Packing", "Rider shortage": "Rider Assignment", "Failed delivery": "Delivery",
              "Delivery delay": "Delivery"}


def derive_scenario_labels(T):
    """Scenario labels from observed facts only (Data Dictionary V1.1 Section 7).

    Primary label (at most one per order) = scenario of the EARLIEST abnormal stage whose signature is observable:
      Cancelled > Stockout > (Multi-stage | Picking delay) > Packing delay > Rider shortage > Failed delivery
      > Delivery delay > Normal flow.   Multi-stage needs the complete picking-origin chain (picking, packing,
    rider wait AND a late delivery). A picking-origin order whose chain is only partly visible gets no stage label.
    Overlay label: Live At Risk / Breached for any order still In Progress/Placed that is At Risk or Breached.
    Returns (label rows without label_id, {order_id: primary scenario}).
    """
    ts = lambda x: pd.Timestamp(x) if isinstance(x, str) and x else None
    O, I, INV, DV, EX, RA = (T[k] for k in ("orders", "order_items", "inventory", "delivery_rider",
                                            "exceptions", "rider_availability"))
    counted = {(r.store_id, r.sku_id): r.counted_shelf_stock for r in INV.itertuples()}
    short = {}
    for r in I.itertuples():
        if pd.notna(r.quantity_picked) and r.quantity_picked < r.quantity_ordered \
                and counted[(r.store_id, r.sku_id)] < r.quantity_ordered:
            short.setdefault(r.order_id, r.sku_id)
    zero = {(r.store_id, ts(r.snapshot_time)) for r in RA.itertuples() if r.riders_available == 0}
    failed = set(DV.order_id[DV.failed_attempt_flag == True])  # noqa: E712
    has_exc = set(EX.order_id.dropna())

    def zero_riders(store, t0, t1):
        h = t0.floor("h")
        while h <= t1:
            if (store, h) in zero:
                return True
            h += pd.Timedelta(hours=1)
        return False

    rows, primary = [], {}
    for r in O.itertuples():
        ps, pe, ks, ke, asg, dl, prom = (ts(x) for x in (r.pick_start, r.pick_end, r.pack_start, r.pack_end,
                                                        r.assignment_time, r.delivery_time, r.promised_delivery_time))
        a_pick = bool(ps and pe and pe - ps > mins(C.PICK_DELAY_MIN))
        a_pack = bool(ks and ke and ke - ks > mins(C.PACK_DELAY_MIN))
        a_wait = bool(ke and asg and asg - ke > mins(C.ASSIGN_DELAY_MIN))
        a_zero = a_wait and zero_riders(r.store_id, ke, asg)
        a_late = bool(asg and dl and dl > prom)
        if r.order_status == "Cancelled":
            lab = "Cancelled"
        elif r.order_id in short:
            lab = "Stockout"
        elif a_pick:
            if a_pack and a_wait and a_late:
                lab = "Multi-stage disruption"
            elif not a_pack and not a_wait:
                lab = "Picking delay"
            else:
                lab = None                  # partly propagated, delivery outcome not yet observable
        elif a_pack:
            lab = "Packing delay"
        elif a_wait:
            lab = "Rider shortage" if a_zero else None
        elif r.order_id in failed:
            lab = "Failed delivery"
        elif a_late:
            lab = "Delivery delay"
        elif r.order_status == "Delivered" and dl and dl <= prom and r.order_id not in has_exc:
            lab = "Normal flow"
        else:
            lab = None
        if lab:
            primary[r.order_id] = lab
            rows.append([lab, r.order_id, r.store_id, short.get(r.order_id) if lab == "Stockout" else None,
                         ROOT_CAUSE.get(lab)])
        if r.order_status in ("Placed", "In Progress") and r.sla_status in ("At Risk", "Breached"):
            rows.append(["Live At Risk / Breached", r.order_id, r.store_id, None, None])
    for r in INV.itertuples():
        if r.system_stock != r.counted_shelf_stock:
            rows.append(["Inventory mismatch", None, r.store_id, r.sku_id, "Inventory"])
    return rows, primary


class Generator:
    def __init__(self):
        self.rng = np.random.default_rng(C.SEED)
        self.orders = []
        self.tables = {}

    # ------------------------------------------------------------------ reference data
    def build_reference(self):
        n = C.N_STORES
        self.sids = [f"ST{i:03d}" for i in range(1, n + 1)]
        zones = ["Zone_A", "Zone_B", "Zone_C"]
        self.store_zone = [zones[((i) // 2) % 3] for i in range(n)]
        self.store_pin = [str(900000 + i + 1) for i in range(n)]
        self.tables["stores"] = pd.DataFrame({
            "store_id": self.sids,
            "store_name": [f"Synthetic Store {i:03d}" for i in range(1, n + 1)],
            "zone": self.store_zone, "pincode": self.store_pin, "status": "Active"})
        self.assoc = {s: [f"AS-{s}-{k:02d}" for k in range(1, C.ASSOCIATES_PER_STORE + 1)] for s in self.sids}
        self.riders = {s: [f"RD-{s}-{k:02d}" for k in range(1, C.RIDERS_PER_STORE + 1)] for s in self.sids}
        self.mgrs = {s: [f"MG-{s}-{k:02d}" for k in range(1, C.MANAGERS_PER_STORE + 1)] for s in self.sids}
        self.skus = [f"SKU_{i:03d}" for i in range(1, C.N_SKUS + 1)]
        rank = self.rng.permutation(C.N_SKUS) + 1
        base_w = 1.0 / rank ** 0.8                      # a few SKUs are much more popular
        self.store_w = [base_w * self.rng.uniform(0.7, 1.3, C.N_SKUS) for _ in range(n)]

    # ------------------------------------------------------------------ inventory
    def build_inventory(self):
        rng, n, k_n = self.rng, C.N_STORES, C.N_SKUS
        counts = [0] * n
        for i in range(C.STOCKOUT_COMBOS):
            counts[i % n] += 1
        self.so_set, self.so_combos = set(), {s: [] for s in range(n)}
        for s in range(n):
            if counts[s]:
                p = self.store_w[s] / self.store_w[s].sum()
                for k in rng.choice(k_n, size=counts[s], replace=False, p=p):
                    self.so_set.add((s, int(k)))
        rest = [(s, k) for s in range(n) for k in range(k_n) if (s, k) not in self.so_set]
        order = rng.permutation(len(rest))
        self.mm_set = {rest[i] for i in order[:C.EXTRA_MISMATCH_ROWS]}
        low = {rest[i] for i in order[C.EXTRA_MISMATCH_ROWS:C.EXTRA_MISMATCH_ROWS + C.LOW_STOCK_ROWS]}
        rows, self.inv_counted = [], {}
        for s in range(n):
            for k in range(k_n):
                if (s, k) in self.so_set:
                    counted = 0 if rng.random() < 0.6 else 1
                    system = counted + int(rng.integers(2, 7))
                    self.so_combos[s].append((k, counted))
                elif (s, k) in self.mm_set:
                    counted = int(rng.integers(20, 61))
                    d = int(rng.integers(1, 6))
                    system = counted + d if rng.random() < 0.7 else counted - d
                elif (s, k) in low:
                    counted = system = int(rng.integers(3, 11))
                else:
                    counted = system = int(rng.integers(15, 81))
                self.inv_counted[(s, k)] = counted
                last = S - secs(rng.uniform(0, 12 * 3600))
                adj = int(rng.choice([-3, -2, -1, 1, 2, 3])) if rng.random() < 0.30 else None
                ret = int(rng.integers(1, 4)) if rng.random() < 0.20 else None
                rows.append([self.sids[s], self.skus[k], system, counted, iso(last), adj, ret])
        self.tables["inventory"] = pd.DataFrame(rows, columns=[c for c, _ in C.SCHEMA["inventory"]])

    # ------------------------------------------------------------------ scenario planning
    def plan_scenarios(self):
        N = C.N_ORDERS
        cnt = {"cancelled": round(N * C.CANCELLATION_RATE)}
        nf = round(N * C.FAILED_DELIVERY_RATE)
        cnt["failed_retry"] = round(nf * C.FAILED_RETRY_SUCCESS_SHARE)
        cnt["failed_noretry"] = nf - cnt["failed_retry"]
        n_prob = round(N * (C.EXCEPTION_RATE - C.FAILED_DELIVERY_RATE))
        for k, v in C.PROBLEM_SPLIT.items():
            cnt[k] = round(n_prob * v)
        cnt["normal"] = N - sum(cnt.values())
        self.pool = cnt

    # ------------------------------------------------------------------ order lines
    def make_lines(self, s, scen):
        rng = self.rng
        nl = int(rng.choice(list(C.LINES_PER_ORDER_WEIGHTS), p=list(C.LINES_PER_ORDER_WEIGHTS.values())))
        qs, qp = list(C.QTY_WEIGHTS), np.array(list(C.QTY_WEIGHTS.values()))
        lines, used = [], set()
        if scen == "stockout":
            cands = self.so_combos[s]
            w = np.array([self.store_w[s][k] for k, _ in cands])
            k, cnt = cands[int(rng.choice(len(cands), p=w / w.sum()))]
            ok = [q for q in qs if q > cnt]
            pw = np.array([C.QTY_WEIGHTS[q] for q in ok])
            q = int(rng.choice(ok, p=pw / pw.sum()))
            lines.append(dict(sku=k, q=q, picked=min(q, cnt), subst=bool(rng.random() < 0.4), stock=True))
            used.add(k)
        need = nl - len(lines)
        avail = [k for k in range(C.N_SKUS) if k not in used and (s, k) not in self.so_set]
        w = np.array([self.store_w[s][k] for k in avail])
        for k in rng.choice(avail, size=need, replace=False, p=w / w.sum()):
            q = int(rng.choice(qs, p=qp))
            q = min(q, self.inv_counted[(s, int(k))])
            lines.append(dict(sku=int(k), q=q, picked=q, subst=False, stock=False))
        mis = False
        if scen == "normal" and rng.random() < C.MISPICK_RATE:
            cand = [l for l in lines if l["q"] >= 2]
            if cand:
                l = cand[int(rng.integers(0, len(cand)))]
                l["picked"] = l["q"] - 1
                mis = True
        lines = [lines[i] for i in rng.permutation(len(lines))]
        return lines, mis

    # ------------------------------------------------------------------ lifecycle timeline (minutes after order_time)
    def sample_timeline(self, scen):
        r = self.rng
        for _ in range(500):
            accept = r.uniform(.5, 2.0)
            pick_d = r.triangular(3, 5.5, 8.5)
            gap = r.uniform(0, 1)
            pack_d = r.triangular(2, 3.5, 5)
            wait = r.triangular(.5, 2, 4.5)
            lag = r.uniform(1, 3)
            transit = r.triangular(6, 8, 11)
            if scen == "picking_delay":
                pick_d = r.uniform(C.PICK_DELAY_MIN + 1, 16)
            elif scen == "packing_delay":
                pack_d = r.uniform(C.PACK_DELAY_MIN + .5, 10)
            elif scen == "rider_shortage":
                wait = r.uniform(C.ASSIGN_DELAY_MIN + 1, 15)
            elif scen == "delivery_delay":
                transit = r.uniform(14, 22)
            elif scen == "multi_stage":
                pick_d, pack_d = r.uniform(11, 18), r.uniform(C.PACK_DELAY_MIN + .5, 9)
                wait, transit = r.uniform(C.ASSIGN_DELAY_MIN + 1, 12), r.triangular(7, 10, 13)
            ps = accept
            pe = ps + pick_d
            ks = pe + gap
            ke = ks + pack_d
            asg = ke + wait
            pu = asg + lag
            dl = pu + transit
            if scen == "delivery_delay":
                dl = max(dl, C.PROMISED_WINDOW_MINUTES + r.uniform(3, 10))
            elif scen == "multi_stage":
                dl = max(dl, C.PROMISED_WINDOW_MINUTES + r.uniform(2, 8))
            failed = None
            if scen in ("failed_retry", "failed_noretry"):
                failed = dl
                dl = failed + r.uniform(8, 14) if scen == "failed_retry" else None
            if scen in ("normal", "stockout", "failed_retry", "failed_noretry"):
                if (failed if failed is not None else dl) > 29:       # keep non-delay scenarios on time
                    continue
            break
        return dict(pick_start=ps, pick_end=pe, pack_start=ks, pack_end=ke, assign=asg,
                    pickup=pu, failed=failed, deliver=dl)

    def live_age(self, scen, mode, tl):
        r, d = self.rng, tl["deliver"]
        if scen == "failed_retry":
            return r.uniform(tl["failed"] + .2, d - .2)
        if scen == "failed_noretry":
            return r.uniform(tl["failed"] + 1, tl["failed"] + 15)
        if mode == "atrisk":                   # 20-30 min old and still undelivered
            hi = min(29.5, d - .5)
            return r.uniform(min(20.5, hi - .5), hi)
        if mode == "breached":                 # older than the promise and (ideally) still undelivered
            hi = d - .5
            return r.uniform(30.5, hi) if hi > 30.5 else 30.2
        lo = tl["pick_end"] + 1 if scen in ("stockout", "packing_delay") else 3
        return r.uniform(lo, 45)

    def regular_time(self):
        r = self.rng
        hours = np.array(list(C.OPERATING_HOURS))
        hw = np.array([C.PEAK_HOUR_WEIGHT.get(int(h), 1.0) for h in hours])
        hw = hw / hw.sum()
        cutoff = S - timedelta(minutes=C.LIVE_WINDOW_MINUTES)
        while True:
            d, h, sec = int(r.integers(0, C.N_DAYS)), int(r.choice(hours, p=hw)), int(r.integers(0, 3600))
            t = C.START_DATE + timedelta(days=d, hours=h, seconds=sec)
            if t <= cutoff:
                return t

    # Stage that must already be visible at the snapshot for the intended scenario's signature to be observable.
    OBSERVABLE_BY = {"normal": "deliver", "picking_delay": "pick_end", "packing_delay": "pack_end",
                     "rider_shortage": "assign", "delivery_delay": "deliver", "multi_stage": "deliver",
                     "stockout": "pick_end", "failed_retry": "failed", "failed_noretry": "failed"}

    def sample_timing(self, scen, mode, live):
        """Draw (timeline, order_time). A regular (non-live) order whose intended scenario would be cut off by
        the snapshot is resampled, so it is never written with a scenario its data cannot show. Live orders are
        deliberately in flight; their label is derived from whatever is observable (see derive_scenario_labels)."""
        for _ in range(200):
            tl = None if scen == "cancelled" else self.sample_timeline(scen)
            ot = (S - secs(self.live_age(scen, mode, tl) * 60)) if live else self.regular_time()
            if live or scen == "cancelled" or ot + mins(tl[self.OBSERVABLE_BY[scen]]) <= S:
                return tl, ot
        raise RuntimeError(f"could not build an observable '{scen}' order")

    # ------------------------------------------------------------------ orders skeleton
    def build_orders(self):
        rng = self.rng
        pool = dict(self.pool)
        specs = list(C.LIVE_FORCED) + [("normal", "any")] * (C.LIVE_ORDERS - len(C.LIVE_FORCED))
        for scen, _ in specs:
            pool[scen] -= 1
        remaining = []
        for k in sorted(pool):
            remaining += [k] * pool[k]
        assert len(remaining) == C.N_ORDERS - C.LIVE_ORDERS and min(pool.values()) >= 0, "scenario pool mismatch"
        remaining = [remaining[i] for i in rng.permutation(len(remaining))]
        sp = np.array(C.STORE_WEIGHTS) / sum(C.STORE_WEIGHTS)
        todo = [(scen, mode, True) for scen, mode in specs] + [(sc, "any", False) for sc in remaining]
        for scen, mode, live in todo:
            s = int(rng.choice(C.N_STORES, p=sp))
            lines, mis = self.make_lines(s, scen)
            tl, ot = self.sample_timing(scen, mode, live)
            self.orders.append(dict(scen=scen, s=s, sid=self.sids[s], ot=ot, lines=lines, mis=mis,
                                    live=live, tl=tl,
                                    dzone=f"{self.store_zone[s]}-{int(rng.integers(1, 4))}",
                                    dpin=str(900100 + 10 * s + int(rng.integers(0, 4)))))
        self.orders.sort(key=lambda o: (o["ot"], o["s"]))
        for i, o in enumerate(self.orders, 1):
            o["oid"] = f"ORD_{i:06d}"
            o["prom"] = o["ot"] + mins(C.PROMISED_WINDOW_MINUTES)
            o["a"] = {k: (o["ot"] + mins(v) if v is not None else None) for k, v in o["tl"].items()} if o["tl"] else {}

    # ------------------------------------------------------------------ derive state at the snapshot
    def derive_state(self):
        rng = self.rng
        for o in self.orders:
            a = o["a"]
            if o["scen"] == "cancelled":
                o.update(stage="Cancelled", ostatus="Cancelled", sla="Not Applicable", dstatus=None,
                         v={k: None for k in ("ps", "pe", "ks", "ke", "asg", "pu", "dl")}, failed_vis=False)
                continue
            v = dict(ps=vis(a["pick_start"]), pe=vis(a["pick_end"]), ks=vis(a["pack_start"]),
                     ke=vis(a["pack_end"]), asg=vis(a["assign"]), pu=vis(a["pickup"]), dl=vis(a["deliver"]))
            failed_vis = a["failed"] is not None and a["failed"] <= S
            if v["dl"]:
                stage = "Delivered"
            elif v["asg"]:
                stage = "Out for Delivery"
            elif v["ke"]:
                stage = "Awaiting Rider"
            elif v["pe"]:
                stage = "Packing"
            elif v["ps"]:
                stage = "Picking"
            else:
                stage = "Placed"
            if v["ke"] is None:
                dstat = None
            elif v["dl"]:
                dstat = "Delivered"
            elif failed_vis:
                dstat = "Failed"
            elif not v["asg"]:
                dstat = "Awaiting Assignment"
            elif not v["pu"]:
                dstat = "Assigned"
            elif S < v["pu"] + mins(1):
                dstat = "Picked Up"
            else:
                dstat = "In Transit"
            if stage == "Placed":
                ostat = "Placed"
            elif stage == "Delivered":
                ostat = "Delivered"
            elif dstat == "Failed":
                ostat = "Delivery Failed"
            else:
                ostat = "In Progress"
            if v["dl"]:
                sla = "On Time" if v["dl"] <= o["prom"] else "Breached"
            else:
                rem = (o["prom"] - S).total_seconds() / 60
                sla = "On Time" if rem > C.AT_RISK_BUFFER_MINUTES else ("At Risk" if rem >= 0 else "Breached")
            o.update(v=v, stage=stage, ostatus=ostat, sla=sla, dstatus=dstat, failed_vis=failed_vis)

    # ------------------------------------------------------------------ riders (Delivery table support)
    def assign_riders(self):
        rng = self.rng
        free = {s: {r: C.START_DATE - timedelta(days=1) for r in self.riders[s]} for s in self.sids}
        todo = sorted([o for o in self.orders if o["v"].get("asg")], key=lambda o: (o["v"]["asg"], o["oid"]))
        for o in todo:
            f, t = free[o["sid"]], o["v"]["asg"]
            idle = [r for r in self.riders[o["sid"]] if f[r] <= t]
            busy = [r for r in self.riders[o["sid"]] if f[r] > t]
            if idle and not (busy and rng.random() < 0.08):
                r, av = pick(rng, idle), "Available"
            else:
                r, av = pick(rng, busy or idle), ("Busy" if busy else "Available")
            a = o["a"]
            end = a["deliver"] if a["deliver"] else a["failed"] + mins(10)
            f[r] = max(f[r], end)
            o["rider"], o["ravail"] = r, av

    # ------------------------------------------------------------------ core tables
    def build_stage_tables(self):
        rng = self.rng
        orows, irows, prow, kro, drow = [], [], [], [], []
        for o in self.orders:
            v, a = o["v"], o["a"]
            tq = sum(l["q"] for l in o["lines"])
            orows.append([o["oid"], o["sid"], iso(o["ot"]), iso(o["prom"]), o["stage"], o["ostatus"], o["sla"],
                          iso(v["ps"]), iso(v["pe"]), iso(v["ks"]), iso(v["ke"]), iso(v["asg"]), iso(v["pu"]),
                          iso(v["dl"]), o["dzone"], o["dpin"], len(o["lines"]), tq])
            for l in o["lines"]:
                irows.append([o["oid"], o["sid"], self.skus[l["sku"]], l["q"],
                              l["picked"] if v["pe"] else None, l["subst"]])
            if v["ps"]:
                picked = sum(l["picked"] for l in o["lines"]) if v["pe"] else None
                prow.append([o["oid"], pick(rng, self.assoc[o["sid"]]), iso(v["ps"]), iso(v["pe"]), tq, picked,
                             o["mis"] if v["pe"] else None])
            if v["ks"]:
                issue = None
                if v["ke"]:
                    issue = bool((a["pack_end"] - a["pack_start"]) > mins(C.PACK_DELAY_MIN) or rng.random() < 0.01)
                kro.append([o["oid"], iso(v["ks"]), iso(v["ke"]), issue])
            if v["ke"]:
                drow.append([o["oid"], o.get("rider") if v["asg"] else None, iso(v["asg"]), iso(v["pu"]),
                             iso(v["dl"]), o["dstatus"], bool(o["failed_vis"]),
                             o.get("ravail") if v["asg"] else None])
        T = self.tables
        for name, rows in [("orders", orows), ("order_items", irows), ("picking", prow),
                           ("packing", kro), ("delivery_rider", drow)]:
            T[name] = pd.DataFrame(rows, columns=[c for c, _ in C.SCHEMA[name]])

    # ------------------------------------------------------------------ rider availability (hourly)
    def build_availability(self):
        rng = self.rng
        short = set()
        for o in self.orders:
            a, v = o["a"], o["v"]
            if o["scen"] in ("rider_shortage", "multi_stage") and v.get("ke"):
                for t in (a["pack_end"], a["assign"]):
                    if t <= S:
                        short.add((o["sid"], t.replace(minute=0, second=0)))
        vol = {}
        for o in self.orders:
            key = (o["sid"], o["ot"].replace(minute=0, second=0))
            vol[key] = vol.get(key, 0) + 1
        rows, pool = [], C.RIDERS_PER_STORE
        for sid in self.sids:
            for d in range(C.N_DAYS):
                for h in range(7, 24):
                    t = C.START_DATE + timedelta(days=d, hours=h)
                    if t > S:
                        continue
                    peak = C.PEAK_HOUR_WEIGHT.get(h, 1.0) > 1
                    shift = int(rng.integers(4, 6)) if peak else int(rng.integers(3, 5))
                    if (sid, t) in short:
                        shift = int(rng.integers(1, 3))
                        busy = shift
                    else:
                        busy = min(shift - 1, int(round(vol.get((sid, t), 0) * 0.9 + rng.uniform(0, 0.8))))
                    rows.append([sid, iso(t), shift - busy, busy, pool - shift])
        self.tables["rider_availability"] = pd.DataFrame(rows, columns=[c for c, _ in C.SCHEMA["rider_availability"]])
        self.short_hours = short

    # ------------------------------------------------------------------ exceptions
    RESOLUTIONS = {
        "Inventory Mismatch": ["Shelf recounted; stock adjustment prepared and system value reconciled.",
                               "Recount confirmed the difference; location audited and adjustment recorded."],
        "Stockout": ["Available units picked; substitution offered for the short line.",
                     "Short line removed from the order and replenishment requested."],
        "Picking Delay": ["Order reassigned to a free picker; picking completed.",
                          "Picker workload re-balanced; order picked and moved to packing."],
        "Packing Delay": ["Packing material restocked; order packed and handed over.",
                          "Packing station backlog cleared; order packed."],
        "Rider Assignment Delay": ["Additional rider assigned from standby.",
                                   "Order held at dispatch until a rider became free; rider assigned."],
        "Delivery Delay": ["Cause traced to an earlier stage; noted for shift review.",
                           "Rider contacted; delivery completed and delay cause recorded."],
        "SLA Risk/Breach": ["Order prioritised at the delayed stage; outcome reviewed by the manager.",
                            "Breach cause recorded as a candidate cause and reviewed."],
        "Failed Delivery Attempt": ["Customer contacted; retry delivery completed.",
                                    "Retry attempt recorded; next step decided by the delivery manager."],
    }

    def lifecycle(self, det, approval, force_open):
        r = self.rng
        t_inv = det + mins(r.uniform(10, 240) if force_open else r.uniform(2, 20))
        L = dict(inv=t_inv, pend=None, dec=None, res=None, decision=None)
        if approval:
            L["pend"] = t_inv + mins(r.uniform(5, 30))
            L["decision"] = str(r.choice(list(C.DECISION_WEIGHTS), p=list(C.DECISION_WEIGHTS.values())))
            L["dec"] = L["pend"] + mins(r.uniform(5, 60))
            L["res"] = L["dec"] + mins(r.uniform(15, 90) if L["decision"] == "Rejected" else r.uniform(1, 10))
        else:
            L["res"] = t_inv + mins(r.uniform(10, 120))
        if force_open and approval and t_inv + mins(5) <= S and r.random() < 0.6:
            # store/SKU mismatch whose adjustment is awaiting a manager decision at the snapshot
            L["pend"] = max(t_inv + mins(5), S - mins(r.uniform(1, 180)))
            L["dec"] = S + mins(r.uniform(10, 300))
            L["res"] = L["dec"] + mins(r.uniform(15, 90) if L["decision"] == "Rejected" else r.uniform(1, 10))
        if force_open and L["res"] <= S:       # store/SKU mismatch stays unreconciled in this snapshot
            L["res"] = S + mins(r.uniform(30, 600))
        return L

    def build_exceptions(self):
        r = self.rng
        cands = []
        P = C.SLA_EXCEPTION_PROB

        def add(cat, o, sku, det, force_open=False, sid=None):
            if det <= S:
                cands.append(dict(cat=cat, o=o, sid=sid or o["sid"], sku=sku, det=det, force=force_open))

        for o in self.orders:
            if o["scen"] == "cancelled":
                continue
            a, prom = o["a"], o["prom"]
            if a["pick_end"] - a["pick_start"] > mins(C.PICK_DELAY_MIN):
                add("Picking Delay", o, None, a["pick_start"] + mins(C.PICK_DELAY_MIN) + secs(r.uniform(0, 60)))
            if a["pack_end"] - a["pack_start"] > mins(C.PACK_DELAY_MIN):
                add("Packing Delay", o, None, a["pack_start"] + mins(C.PACK_DELAY_MIN) + secs(r.uniform(0, 60)))
            if a["assign"] - a["pack_end"] > mins(C.ASSIGN_DELAY_MIN):
                add("Rider Assignment Delay", o, None, a["pack_end"] + mins(C.ASSIGN_DELAY_MIN) + secs(r.uniform(0, 60)))
            if a["deliver"] is None or a["deliver"] > prom:
                add("Delivery Delay", o, None, prom + secs(r.uniform(0, 60)))
            if a["failed"] is not None:
                add("Failed Delivery Attempt", o, None, a["failed"] + secs(r.uniform(0, 60)))
            st = [l for l in o["lines"] if l["stock"]]
            if st and o["v"]["pe"]:
                add("Stockout", o, self.skus[st[0]["sku"]], a["pick_end"])
            if o["sla"] == "Breached" and r.random() < P:
                add("SLA Risk/Breach", o, None, prom + secs(r.uniform(30, 180)))
            elif o["sla"] == "At Risk" and r.random() < P:
                add("SLA Risk/Breach", o, None, min(prom - mins(C.AT_RISK_BUFFER_MINUTES) + secs(r.uniform(0, 60)), S))
        for s, k in sorted(self.mm_set | self.so_set):
            if r.random() < C.MISMATCH_EXCEPTION_PROB:
                add("Inventory Mismatch", None, self.skus[k], S - secs(r.uniform(1, 30) * 3600),
                    force_open=True, sid=self.sids[s])
        cands.sort(key=lambda c: (c["det"], c["cat"], c["sid"], c["sku"] or ""))
        rows = []
        for i, c in enumerate(cands, 1):
            appr = bool(r.random() < C.APPROVAL_APPLICABLE_SHARE)
            L = self.lifecycle(c["det"], appr, c["force"])
            if S >= L["res"]:
                status = "Resolved/Closed"
            elif appr and S >= L["dec"]:
                status = "Under Investigation"
            elif appr and S >= L["pend"]:
                status = "Pending Approval"
            elif S >= L["inv"]:
                status = "Under Investigation"
            else:
                status = "Open"
            sid = c["sid"]
            if c["cat"] in ("Rider Assignment Delay", "Delivery Delay", "SLA Risk/Breach", "Failed Delivery Attempt"):
                pool = self.mgrs[sid]
            else:
                pool = self.assoc[sid] if r.random() < 0.6 else self.mgrs[sid]
            assigned = pick(r, pool) if (status != "Open" or r.random() < 0.35) else None
            decided = appr and S >= L["dec"]
            resolution = None
            if status == "Resolved/Closed":
                resolution = pick(r, self.RESOLUTIONS[c["cat"]])
                if appr and L["decision"] == "Rejected":
                    resolution = "Recommended action rejected by the approver; alternative action taken. " + resolution
            rows.append([f"EXC_{i:06d}", c["cat"], c["o"]["oid"] if c["o"] else None, sid, c["sku"], iso(c["det"]),
                         status, assigned, appr, pick(r, self.mgrs[sid]) if decided else None,
                         L["decision"] if decided else None, iso(L["dec"]) if decided else None,
                         resolution, iso(L["res"]) if status == "Resolved/Closed" else None])
        self.tables["exceptions"] = pd.DataFrame(rows, columns=[c for c, _ in C.SCHEMA["exceptions"]])

    # ------------------------------------------------------------------ SOP knowledge
    def build_sop(self):
        rows = []
        for doc_id, title, cat, owner, ver, texts, status in DOCS:
            for j, text in enumerate(texts):
                rows.append([doc_id, title, f"S{j + 1}", text, ver, owner, status.get(j, "Approved"), cat, "Synthetic"])
        self.tables["sop_knowledge"] = pd.DataFrame(rows, columns=[c for c, _ in C.SCHEMA["sop_knowledge"]])

    # ------------------------------------------------------------------ scenario labels
    INTENDED_NAME = {"normal": "Normal flow", "picking_delay": "Picking delay", "packing_delay": "Packing delay",
                     "rider_shortage": "Rider shortage", "delivery_delay": "Delivery delay",
                     "multi_stage": "Multi-stage disruption", "stockout": "Stockout", "failed_retry": "Failed delivery",
                     "failed_noretry": "Failed delivery", "cancelled": "Cancelled"}

    def build_labels(self):
        """Labels are derived from the finished tables (facts), never copied from the intended category."""
        rows, primary = derive_scenario_labels(self.tables)
        for o in self.orders:               # intended-vs-observed gate for regular orders (live orders may truncate)
            if not o["live"] and primary.get(o["oid"]) != self.INTENDED_NAME[o["scen"]]:
                raise AssertionError(f"{o['oid']}: intended '{o['scen']}' but data shows '{primary.get(o['oid'])}'")
        rows = [[f"LBL_{i:06d}"] + r for i, r in enumerate(rows, 1)]
        self.tables["scenario_labels"] = pd.DataFrame(rows, columns=[c for c, _ in C.SCHEMA["scenario_labels"]])

    # ------------------------------------------------------------------ write
    def write(self):
        C.RAW_DIR.mkdir(parents=True, exist_ok=True)
        for name, df in self.tables.items():
            cols = [c for c, _ in C.SCHEMA[name]]
            out = df[cols].copy()
            for c, t in C.SCHEMA[name]:
                if t == "int":
                    out[c] = out[c].astype("Int64")
                elif t == "bool":
                    out[c] = out[c].map(lambda x: "" if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NA
                                        else ("True" if bool(x) else "False"))
            out.to_csv(C.RAW_DIR / C.FILES[name], index=False, lineterminator="\n")

    def run(self):
        self.build_reference()
        self.build_inventory()
        self.plan_scenarios()
        self.build_orders()
        self.derive_state()
        self.assign_riders()
        self.build_stage_tables()
        self.build_availability()
        self.build_exceptions()
        self.build_sop()
        self.build_labels()
        self.write()
        return self


def checksums():
    return {C.FILES[n]: hashlib.sha256((C.RAW_DIR / C.FILES[n]).read_bytes()).hexdigest() for n in C.FILES}


def main():
    print(f"MacroOps AI synthetic data generator | seed={C.SEED} | orders={C.N_ORDERS} | snapshot={C.SNAPSHOT_TIME}")
    Generator().run()
    C.VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    report, ok = V.run_and_report(C.RAW_DIR)
    (C.VALIDATION_DIR / "validation_report.txt").write_text(report, encoding="utf-8")
    sums = checksums()
    (C.VALIDATION_DIR / "checksums_sha256.txt").write_text(
        "\n".join(f"{h}  {n}" for n, h in sums.items()) + "\n", encoding="utf-8")
    print(report)
    if not ok:
        print("CRITICAL VALIDATION FAILURE - dataset must not be used.", file=sys.stderr)
        sys.exit(1)
    print("Generation complete. Files written to", C.RAW_DIR)


if __name__ == "__main__":
    main()
