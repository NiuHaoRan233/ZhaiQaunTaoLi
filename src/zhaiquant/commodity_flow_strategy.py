"""Incremental, long-only commodity-option paper strategy. No broker execution.

Integer cents PER CONTRACT throughout. The replay clock reproduces the frozen
2026-09-12 flow candidates; paper_arrival uses actual decision arrival times.
"""
from collections import Counter
from copy import deepcopy

FAMILY = "commodity_flow_20260913_v0_1_r2"
VARIANTS = ("flow_patient", "flow_entry")
FEE = 170


class FlowAccount:
    def __init__(self, code, variant, initial_cents, tick_cents, clock="replay", state=None):
        if variant not in VARIANTS or clock not in ("replay", "paper_arrival"):
            raise ValueError("Unknown immutable strategy identity")
        if type(initial_cents) is not int or type(tick_cents) is not int or min(initial_cents, tick_cents) <= 0:
            raise ValueError("Positive integer cents required")
        self.code, self.variant, self.tick = code, variant, tick_cents
        self.model = f"{FAMILY}_{variant}_{clock}"
        self.s = dict(model_id=self.model, code=code, initial=initial_cents, tick=tick_cents,
                      cash=initial_cents, inventory=0, basis=0, realized=0, fees=0,
                      order=None, order_count=0, fill_count=0, cycle=None, cycle_count=0,
                      completed_net=0, winning=0, losing=0, last_ts=None, session=None,
                      previous_valid=False, flow=[], entry_mid=None, entry_spread=None,
                      entry_session=None, risk_since=None, patient_released=False,
                      last_bid=0, last_quote_ts=None, peak=initial_cents, drawdown=0,
                      holding_ms=0, turnover=0, rejects={}, daily={}, last_pnl=0,
                      latest_reason="waiting_for_market", pause_reason=None)
        if state is not None:
            if any(state.get(k) != self.s[k] for k in ("model_id", "code", "initial", "tick")):
                raise ValueError("Account contract differs from persisted state")
            self.s = deepcopy(state)
            self._invariant()

    def snapshot(self):
        return deepcopy(self.s)

    def _invariant(self):
        s = self.s
        if s["cash"] < 0 or s["inventory"] not in (0, 1):
            raise ValueError("Cash/inventory invariant violated")
        if bool(s["cycle"]) != bool(s["inventory"]):
            raise ValueError("Open cycle and inventory disagree")
        if s["cash"] + s["inventory"] * s["last_bid"] - s["initial"] != (
                s["realized"] + s["inventory"] * s["last_bid"] - s["basis"] - s["fees"]):
            raise ValueError("Cash/profit reconciliation failed")

    def _cancel(self, ts, reason):
        order = self.s["order"]
        self.s["order"] = None
        return [] if order is None else [dict(model_id=self.model, code=self.code,
                    order_id=order["id"], ts=ts, reason=reason)]

    def pause(self, ts, reason):
        """Cancel synthetic intent, never erase inventory or cash."""
        cancels = self._cancel(ts, reason)
        self.s.update(flow=[], risk_since=None, previous_valid=False,
                      pause_reason=reason, latest_reason=reason)
        return cancels

    def step(self, e, date, decision_ts=None, allow_entry=True):
        s = self.s
        if s["last_ts"] is not None and e.ts <= s["last_ts"]:
            raise ValueError("Events must be strictly increasing per contract")
        decision_ts = e.ts if decision_ts is None else decision_ts
        if decision_ts < e.ts:
            raise ValueError("Decision cannot predate source market time")
        orders, fills, cycles, cancels = [], [], [], []
        previous_ts = s["last_ts"]
        if previous_ts is not None:
            s["holding_ms"] += s["inventory"] * (e.ts - previous_ts)
        if e.session != s["session"]:
            cancels += self._cancel(e.ts, "session_change")
            s.update(session=e.session, flow=[])
        if not allow_entry and s["order"] and s["order"]["side"] == "buy":
            cancels += self._cancel(decision_ts, "entry_disabled")

        # Settle a previously active order before using the newly observed book.
        o = s["order"]
        if (o and o["active_ts"] <= e.previous_ts and o["created_ts"] < e.ts
                and e.quantity > 0 and e.single):
            opposite = "sell" if o["side"] == "buy" else "buy"
            reachable = e.last <= o["price"] if o["side"] == "buy" else e.last >= o["price"]
            if e.strict_side == opposite and reachable:
                price = o["price"]
                if o["side"] == "buy":
                    if s["inventory"] or s["cash"] < price + FEE:
                        raise ValueError("Unfunded buy")
                    s.update(cash=s["cash"] - price - FEE, inventory=1, basis=price,
                             cycle=dict(entry_ts=e.ts, entry_price_cents=price, quantity=1,
                                        gross_cents=0, fees_cents=FEE),
                             entry_mid=o["entry_mid_twice"], entry_spread=o["entry_spread"],
                             entry_session=e.session, risk_since=None, patient_released=False)
                else:
                    if s["inventory"] != 1:
                        raise ValueError("Naked sell")
                    cycle = s["cycle"]
                    cycle.update(gross_cents=price - cycle["entry_price_cents"], fees_cents=2*FEE)
                    closed = dict(**cycle, exit_ts=e.ts,
                                  duration_seconds=(e.ts-cycle["entry_ts"])/1000,
                                  net_cents=cycle["gross_cents"]-cycle["fees_cents"])
                    cycles.append(closed)
                    s.update(cash=s["cash"]+price-FEE, inventory=0,
                             realized=s["realized"]+price-s["basis"], basis=0, cycle=None)
                    s["cycle_count"] += 1
                    s["completed_net"] += closed["net_cents"]
                    s["winning"] += int(closed["net_cents"] > 0)
                    s["losing"] += int(closed["net_cents"] < 0)
                s["fees"] += FEE
                s["turnover"] += price
                s["fill_count"] += 1
                fills.append(dict(model_id=self.model, code=self.code, order_id=o["id"],
                    ts=e.ts, side=o["side"], price_cents=price, quantity=1, fee_cents=FEE,
                    inventory=s["inventory"], cash_cents=s["cash"], kind="passive",
                    created_ts=o["created_ts"], active_ts=o["active_ts"], source_last_cents=e.last,
                    source_quantity=min(e.quantity, 1), source_side=e.side,
                    source_strict_side=e.strict_side, source_previous_ts=e.previous_ts,
                    source_last_contract_evidence=e.single, date=date))
                s["order"] = None

        valid = 0 < e.bid < e.ask and min(e.bid_qty, e.ask_qty) > 0
        if not valid or previous_ts is not None and e.ts-previous_ts > 60000:
            s["flow"] = []
        while s["flow"] and s["flow"][0][0] < e.ts-300000:
            s["flow"].pop(0)
        if valid and e.quantity > 0 and e.single and e.strict_side in ("buy", "sell"):
            s["flow"].append([e.ts, e.strict_side])
        if valid:
            s.update(last_bid=e.bid, last_quote_ts=e.ts)
            improved = e.ask-e.bid > self.tick
            buy, sell = e.bid+(self.tick if improved else 0), e.ask-(self.tick if improved else 0)
            side = "sell" if s["inventory"] else "buy"
            price = sell if s["inventory"] else buy
            reason, allowed = "current_top", True
            if s["inventory"] and self.variant == "flow_patient":
                if e.session != s["entry_session"] or e.ts-s["cycle"]["entry_ts"] >= 300000:
                    s["patient_released"] = True
                if previous_ts is not None and (e.ts-previous_ts > 60000 or not s["previous_valid"]):
                    s["risk_since"] = None
                if e.bid+e.ask < s["entry_mid"]-2*s["entry_spread"]:
                    if s["risk_since"] is None:
                        s["risk_since"] = e.ts
                    if e.ts-s["risk_since"] >= 30000:
                        s["patient_released"] = True
                else:
                    s["risk_since"] = None
                if not s["patient_released"]:
                    floor = ((s["cycle"]["entry_price_cents"]+2*FEE+2*self.tick-1)//self.tick)*self.tick
                    if price < floor:
                        price, reason = floor, "bounded_cost_exit"
            if not s["inventory"]:
                if not allow_entry:
                    allowed, reason = False, "entry_disabled"
                elif sell-buy < 2*FEE+self.tick:
                    allowed, reason = False, "insufficient_net_edge"
                elif {x[1] for x in s["flow"]} != {"buy", "sell"}:
                    allowed, reason = False, "no_recent_two_sided_flow"
            if allowed and (s["inventory"] or s["cash"] >= price+FEE):
                old = s["order"]
                if old is None or (old["side"], old["price"]) != (side, price):
                    cancels += self._cancel(decision_ts, "reprice")
                    s["order_count"] += 1
                    o = dict(id=s["order_count"], model_id=self.model, code=self.code,
                             side=side, price=price, quantity=1, created_ts=decision_ts,
                             due_ts=decision_ts, reason=reason)
                    if side == "buy":
                        o.update(entry_mid_twice=e.bid+e.ask, entry_spread=e.ask-e.bid)
                    orders.append(o)
                    s["order"] = dict(o, active_ts=decision_ts)
            else:
                if allowed:
                    reason = "insufficient_cash"
                cancels += self._cancel(decision_ts, reason)
                s["rejects"][reason] = s["rejects"].get(reason, 0)+1
            s["latest_reason"] = reason
            s["pause_reason"] = None
        else:
            cancels += self._cancel(decision_ts, "invalid_book")
            s.update(risk_since=None, latest_reason="invalid_book")
        s.update(last_ts=e.ts, previous_valid=valid)
        pnl = s["cash"]+s["inventory"]*s["last_bid"]-s["initial"]
        s["peak"] = max(s["peak"], pnl+s["initial"])
        s["drawdown"] = max(s["drawdown"], s["peak"]-pnl-s["initial"])
        if date not in s["daily"]:
            s["daily"][date] = dict(date=date, opening_pnl_cents=s["last_pnl"], fill_count=0)
        d = s["daily"][date]
        d.update(pnl_cny=(pnl-d["opening_pnl_cents"])/100, cumulative_pnl_cny=pnl/100,
                 end_inventory=s["inventory"], last_ts=e.ts)
        d["fill_count"] += len(fills)
        s["last_pnl"] = pnl
        self._invariant()
        return dict(orders=orders, fills=fills, cycles=cycles, cancels=cancels,
                    curve=(e.ts, pnl, s["inventory"]))

    def daily(self):
        return [{k: v for k, v in d.items() if k != "opening_pnl_cents"}
                for d in self.s["daily"].values()]

    def summary(self, asof_ms=None):
        s = self.s
        now = asof_ms if asof_ms is not None else s["last_ts"]
        age = (now-s["last_quote_ts"])/1000 if now is not None and s["last_quote_ts"] else None
        pnl = s["cash"]+s["inventory"]*s["last_bid"]-s["initial"]
        return dict(model_id=self.model, code=self.code, mode=self.variant,
                    initial_cash_cny=s["initial"]/100, pnl_cny=pnl/100,
                    realized_gross_cny=s["realized"]/100,
                    tail_gross_cny=(s["inventory"]*s["last_bid"]-s["basis"])/100,
                    fees_cny=s["fees"]/100, end_cash_cny=s["cash"]/100,
                    end_inventory=s["inventory"], complete_cycles=s["cycle_count"],
                    completed_cycle_net_cny=s["completed_net"]/100,
                    open_cycle_contribution_cny=(pnl-s["completed_net"])/100,
                    filled_contract_sides=s["fill_count"], fill_count=s["fill_count"],
                    order_count=s["order_count"], max_drawdown_cny=s["drawdown"]/100,
                    holding_seconds=s["holding_ms"]/1000, winning_cycles=s["winning"],
                    losing_cycles=s["losing"], last_quote_ts=s["last_quote_ts"],
                    last_quote_age_seconds=age, stale_tail=bool(s["inventory"] and (age is None or age > 60)),
                    latest_reason=s["latest_reason"], pause_reason=s["pause_reason"],
                    pending_order=deepcopy(s["order"]), evidence="L1_latest_contract_only",
                    fee_per_side_cny=1.7, delay_ms=0, capacity=1,
                    continuous_cash_and_inventory=True, daytime_only=True)
