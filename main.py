"""Pump.fun replay bot: capital preservation first, selective momentum second.

Two sleeves share the account:

* Probe sleeve (guardrails). The bar for a place is a round trip of at least
  0.01 SOL on each of at least 20 coins with max drawdown <= 50%. Probes buy
  0.011 SOL of a liquid, not-crashing curve and sell it whole on the next
  batch after the buy lands, paced over the first ~90 minutes of block time
  until 24 coins have round-tripped. Each probe risks only the fee drag
  (~0.0005 SOL) plus two slots of price movement.

* Main sleeve (selective momentum). At most 4 positions, 0.10 SOL each, no
  adds. A curve qualifies only when it is mature (age and real reserves),
  its last 40 slots show strong net inflow from many distinct buyers, the
  price is up moderately and sitting at its window high, inflow is
  accelerating, the push is still live over the last 10 slots, and the
  coin's creator has not been seen selling. Exits are mechanical: -14%
  stop, trailing stop once +20%, hard take profit at +60%, flow fade, a
  big recorded sell, a creator sell, or a time limit. Buys carry 800 bps
  slippage so a coin that runs away inside the 2-slot latency is refused
  for free instead of chased; sells always go out at 10000 bps. (A
  graduation-ride variant that bought the run to the 85 SOL completion
  was tried and dropped: it caught too few completions and one rug cost
  more than all its wins.)

An equity brake stops new main entries after a 10% drawdown from the peak
(close-value marked) equity, and main entries stop late in block time so the
replay's end-of-window flatten rarely catches a fresh position.

Deterministic by construction: no clock, no environment, no randomness;
iteration is over insertion-ordered dicts fed by the batch stream, and sets
are only tested for membership.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402

SOL = makefun_bot.LAMPORTS_PER_SOL
CURVE_FEE = 0.0125  # protocol 95 bps + creator 30 bps, charged on buys and sells

# ---- main sleeve parameters (SOL / slots): selective momentum ----
MAIN_STAKE = 100_000_000         # 0.10 SOL first buy; there are no adds (v1's adds amplified losers)
MAIN_MAX_POSITIONS = 4
MAIN_MAX_EXPOSURE = 400_000_000  # 0.40 SOL across main positions, at cost
ENTRY_WINDOW = 40                # slots of tape behind an entry decision
ENTRY_MIN_AGE = 250              # slots since CreateEvent
ENTRY_MIN_RQ = 2_500_000_000     # 2.5 SOL real reserves
ENTRY_MAX_RQ = 75_000_000_000    # 75 SOL real reserves
ENTRY_MIN_NET = 1_200_000_000    # 1.2 SOL net inflow over the window
ENTRY_MIN_BUYS = 5
ENTRY_MIN_BUYERS = 5
ENTRY_MIN_BUY_SHARE = 0.65       # buy SOL / total SOL over the window
ENTRY_MIN_UP = 0.08              # price +8% over the window ...
ENTRY_MAX_UP = 1.20              # ... but not more than +120%
ENTRY_NEAR_HIGH = 0.97           # price at least 97% of the window high
ENTRY_PUSH_WINDOW = 10           # the push must still be live: net flow over the last 10 slots
ENTRY_MIN_PUSH = 300_000_000     # ... at least +0.3 SOL
BUY_SLIP_BPS = 800
STOP_LOSS = 0.14                 # sell at value < cost * (1 - 0.14)
TRAIL_ARM = 0.20                 # trailing stop arms at +20%
TRAIL_DROP = 0.18                # and fires 18% under the peak value
TAKE_PROFIT = 0.60               # sell everything at +60%
FADE_WINDOW = 20                 # slots
FADE_NET = -800_000_000          # net flow under -0.8 SOL over the fade window
BIG_SELL = 1_500_000_000         # a single recorded sell of 1.5 SOL exits
MAX_HOLD = 900                   # slots
MAX_HOLD_UP = 0.10               # the time limit is waived while up >= 10%
DD_BRAKE = 0.10                  # no new main entries after a 10% equity drawdown
LATE_STAKE_CUT = 5 * 3600        # block seconds after which stakes halve
LATE_STOP = 5 * 3600 + 1800      # block seconds after which main entries stop

# ---- probe sleeve parameters ----
PROBE_BUY = 11_000_000           # 0.011 SOL, just over the 0.01 round-trip bar
PROBE_TARGET = 22                # coins to round-trip (bar is 20)
PROBE_PACE_SECS = 5400           # spread the target over ~90 minutes of block time
PROBE_MIN_RQ = 3_000_000_000     # 3 SOL real reserves
PROBE_MIN_AGE = 200
PROBE_FLOW_WINDOW = 15
PROBE_MIN_FLOW = 0               # not crashing: net flow over 15 slots >= 0
PROBE_MIN_MOVE = -0.02           # and price not down more than 2% ...
PROBE_MAX_MOVE = 0.10            # ... nor up more than 10% over the same window
PROBE_SLIP_BPS = 500

SELL_SLIP_BPS = 10_000


class Tape:
    __slots__ = ("created", "creator", "dev_sold", "trades")

    def __init__(self):
        self.created = None
        self.creator = None
        self.dev_sold = False
        self.trades = []  # (slot, is_buy, sol_lamports, user, price)


class Held:
    __slots__ = ("opened", "peak_value", "added", "exiting", "last_buy_price")

    def __init__(self, opened, price):
        self.opened = opened
        self.peak_value = 0
        self.added = False
        self.exiting = False
        self.last_buy_price = price


tapes = {}
held = {}
probes = {}          # mint -> True once its sell has been sent
probed = set()       # mints ever probed (membership only)
entered = set()      # mints that ever held a main position (membership only)
tripped = set()      # mints with a completed round trip >= 0.01 SOL (membership only)
bought_at = {}       # mint -> largest filled buy quote_lamports (for round-trip size)
prev_positions = set()
first_bt = None
last_bt = None
peak_equity = None


def tape_of(mint):
    t = tapes.get(mint)
    if t is None:
        t = Tape()
        tapes[mint] = t
    return t


def window_stats(t, now, window):
    """(net, buy_sol, sell_sol, buys, buyers set, up, near_high_ok, net_new, net_old, move)
    over trades with slot in (now - window, now]."""
    from_slot = now - window
    mid = now - window / 2
    net = 0
    buy_sol = 0
    sell_sol = 0
    buys = 0
    buyers = set()
    start_price = -1.0
    high = 0.0
    last = 0.0
    net_new = 0
    net_old = 0
    for slot, is_buy, sol, user, price in t.trades:
        if slot <= from_slot:
            start_price = price
            continue
        if start_price < 0:
            start_price = price
        signed = sol if is_buy else -sol
        net += signed
        if slot <= mid:
            net_old += signed
        else:
            net_new += signed
        if is_buy:
            buy_sol += sol
            buys += 1
            buyers.add(user)
        else:
            sell_sol += sol
        if price > high:
            high = price
        last = price
    if start_price <= 0 or last <= 0:
        return None
    up = last / start_price - 1.0
    return {
        "net": net,
        "buy_sol": buy_sol,
        "sell_sol": sell_sol,
        "buys": buys,
        "buyers": len(buyers),
        "up": up,
        "near_high": high > 0 and last >= high * ENTRY_NEAR_HIGH,
        "net_new": net_new,
        "net_old": net_old,
    }


def flow(t, now, window):
    net = 0
    for slot, is_buy, sol, _user, _price in reversed(t.trades):
        if slot <= now - window:
            break
        net += sol if is_buy else -sol
    return net


def close_value(tokens, curve, tx_fee):
    """What selling `tokens` now would net, in lamports (float): curve quote
    less curve fees less one tx fee, on the shim's latest reserves."""
    if tokens <= 0:
        return 0.0
    gross = tokens * curve.virtual_quote_reserves / (curve.virtual_token_reserves + tokens)
    return gross * (1.0 - CURVE_FEE) - tx_fee


def entry_ok(t, now, rq):
    """Selective-momentum entry: a mature curve with strong, broad,
    accelerating inflow, the price up moderately at its window high, the
    push still live, and the creator not seen selling."""
    if t.created is None or now - t.created < ENTRY_MIN_AGE:
        return False
    if rq < ENTRY_MIN_RQ or rq > ENTRY_MAX_RQ:
        return False
    if t.dev_sold:
        return False
    s = window_stats(t, now, ENTRY_WINDOW)
    if s is None:
        return False
    if s["buys"] < ENTRY_MIN_BUYS or s["buyers"] < ENTRY_MIN_BUYERS:
        return False
    if s["net"] < ENTRY_MIN_NET:
        return False
    total = s["buy_sol"] + s["sell_sol"]
    if total <= 0 or s["buy_sol"] / total < ENTRY_MIN_BUY_SHARE:
        return False
    if s["up"] < ENTRY_MIN_UP or s["up"] > ENTRY_MAX_UP:
        return False
    if not s["near_high"]:
        return False
    if s["net_new"] <= 0:
        return False
    if s["net_old"] > 0 and s["net_new"] < s["net_old"]:
        return False
    if flow(t, now, ENTRY_PUSH_WINDOW) < ENTRY_MIN_PUSH:
        return False
    return True


def on_batch(state, batch):
    global first_bt, last_bt, peak_equity, prev_positions
    now = state.slot
    caps = state.hello["caps"]
    tx_fee = state.tx_fee_lamports()

    # ---- fold fills into round-trip bookkeeping ----
    for f in batch["fills"]:
        if f["status"] != "filled" or f["client_id"].startswith("_"):
            continue
        mint = f["mint"]
        if f["side"] == "buy":
            q = int(f["quote_lamports"])
            if q > bought_at.get(mint, 0):
                bought_at[mint] = q

    # positions that vanished since the last batch complete a round trip
    current = set(state.positions)
    for mint in prev_positions:
        if mint not in current and bought_at.get(mint, 0) >= 10_000_000:
            tripped.add(mint)
    prev_positions = current

    # ---- fold events into tapes ----
    traded = []
    traded_seen = set()
    big_sells = set()
    dev_sells = set()
    for e in batch["events"]:
        bt = e.get("block_time")
        if bt is not None:
            bt = int(bt)
            if first_bt is None:
                first_bt = bt
            if last_bt is None or bt > last_bt:
                last_bt = bt
        kind = e["event"]
        mint = e["mint"]
        if kind == "CreateEvent":
            t = tape_of(mint)
            t.created = e["slot"]
            t.creator = e.get("creator") or e.get("user")
        elif kind == "TradeEvent":
            t = tape_of(mint)
            if t.creator is None and e.get("creator"):
                t.creator = e["creator"]
            price = int(e["virtual_quote_reserves"]) / int(e["virtual_token_reserves"])
            sol = int(e["sol_amount"])
            t.trades.append((e["slot"], bool(e["is_buy"]), sol, e["user"], price))
            if mint not in traded_seen:
                traded_seen.add(mint)
                traded.append(mint)
            if not e["is_buy"] and sol >= BIG_SELL:
                big_sells.add(mint)
            if not e["is_buy"] and t.creator is not None and e["user"] == t.creator:
                t.dev_sold = True
                dev_sells.add(mint)
    keep = max(ENTRY_WINDOW, FADE_WINDOW, PROBE_FLOW_WINDOW) + 5
    for mint in traded:
        t = tapes[mint]
        cut = 0
        while cut < len(t.trades) and t.trades[cut][0] <= now - keep:
            cut += 1
        if cut:
            del t.trades[:cut]

    pending_mints = set()
    pending_sells = set()
    for p in state.pending.values():
        pending_mints.add(p.order["mint"])
        if p.order["side"] == "sell":
            pending_sells.add(p.order["mint"])

    orders = []
    order_cap = caps["max_orders_per_batch"]

    # ---- exits: main positions ----
    for mint in list(held):
        h = held[mint]
        pos = state.positions.get(mint)
        if pos is None:
            if mint not in pending_mints:
                del held[mint]
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        value = close_value(pos.token_amount, curve, tx_fee)
        if value > h.peak_value:
            h.peak_value = value
        cost = pos.cost_lamports
        exit_now = h.exiting
        if not exit_now and value < cost * (1.0 - STOP_LOSS):
            exit_now = True
        if not exit_now and h.peak_value >= cost * (1.0 + TRAIL_ARM) and value < h.peak_value * (1.0 - TRAIL_DROP):
            exit_now = True
        if not exit_now and value >= cost * (1.0 + TAKE_PROFIT):
            exit_now = True
        if not exit_now and flow(tapes[mint], now, FADE_WINDOW) < FADE_NET:
            exit_now = True
        if not exit_now and mint in big_sells:
            exit_now = True
        if not exit_now and mint in dev_sells:
            exit_now = True
        if not exit_now and now - h.opened > MAX_HOLD and value < cost * (1.0 + MAX_HOLD_UP):
            exit_now = True
        if not exit_now:
            continue
        h.exiting = True
        if mint in pending_sells:
            continue
        if len(orders) >= order_cap:
            break
        orders.append({"side": "sell", "mint": mint, "token_amount": pos.token_amount,
                       "max_slippage_bps": SELL_SLIP_BPS})

    # ---- exits: probes sell whole on the first batch after the buy lands ----
    for mint in list(probes):
        pos = state.positions.get(mint)
        if pos is None:
            if mint not in pending_mints:
                del probes[mint]
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete or mint in pending_mints:
            continue
        if len(orders) >= order_cap:
            break
        orders.append({"side": "sell", "mint": mint, "token_amount": pos.token_amount,
                       "max_slippage_bps": SELL_SLIP_BPS})
        probes[mint] = True

    # ---- equity + drawdown brake ----
    equity = float(state.cash)
    for mint, pos in state.positions.items():
        curve = state.curves.get(mint)
        if curve is not None and not curve.complete:
            equity += max(0.0, close_value(pos.token_amount, curve, tx_fee))
    if peak_equity is None or equity > peak_equity:
        peak_equity = equity
    dd_braked = peak_equity > 0 and equity < peak_equity * (1.0 - DD_BRAKE)

    elapsed = (last_bt - first_bt) if (first_bt is not None and last_bt is not None) else 0
    free = state.free_cash_lamports()
    committed = state.committed_position_count()

    # ---- entries: main sleeve ----
    if not dd_braked and elapsed < LATE_STOP:
        stake = MAIN_STAKE if elapsed < LATE_STAKE_CUT else MAIN_STAKE // 2
        exposure = sum(state.positions[m].cost_lamports for m in held if m in state.positions)
        for mint in traded:
            if len(orders) >= order_cap:
                break
            if mint in pending_mints or mint in probes or mint in state.positions:
                continue
            curve = state.curves.get(mint)
            if curve is None or curve.complete:
                continue
            h = held.get(mint)
            if h is not None:
                continue  # already held; positions are never added to
            if mint in entered:
                continue
            if len(held) >= MAIN_MAX_POSITIONS or committed >= caps["max_open_positions"]:
                continue
            if exposure >= MAIN_MAX_EXPOSURE:
                continue
            if not entry_ok(tapes[mint], now, curve.real_quote_reserves):
                continue
            amount = min(stake, state.max_buy_lamports(mint), free - tx_fee)
            if amount < 10_000_000:
                continue
            price = curve.virtual_quote_reserves / curve.virtual_token_reserves
            orders.append({"side": "buy", "mint": mint, "quote_lamports": amount,
                           "max_slippage_bps": BUY_SLIP_BPS})
            held[mint] = Held(now, price)
            entered.add(mint)
            free -= amount + tx_fee
            committed += 1
            exposure += amount

    # ---- entries: probe sleeve (paced) ----
    if len(tripped) < PROBE_TARGET and committed < caps["max_open_positions"]:
        pace = min(1.0, elapsed / PROBE_PACE_SECS) if PROBE_PACE_SECS > 0 else 1.0
        due = int(PROBE_TARGET * pace + 0.999)
        active_probes = len(probes)
        if len(tripped) + active_probes < due:
            for mint in traded:
                if len(orders) >= order_cap or len(probes) >= 1:
                    break
                if mint in probed or mint in held or mint in entered:
                    continue
                if mint in pending_mints or mint in state.positions:
                    continue
                curve = state.curves.get(mint)
                if curve is None or curve.complete:
                    continue
                t = tapes[mint]
                if t.created is None or now - t.created < PROBE_MIN_AGE:
                    continue
                if curve.real_quote_reserves < PROBE_MIN_RQ:
                    continue
                if flow(t, now, PROBE_FLOW_WINDOW) < PROBE_MIN_FLOW:
                    continue
                s = window_stats(t, now, PROBE_FLOW_WINDOW)
                if s is None or s["up"] < PROBE_MIN_MOVE or s["up"] > PROBE_MAX_MOVE:
                    continue
                amount = min(PROBE_BUY, state.max_buy_lamports(mint), free - tx_fee)
                if amount < 10_000_000:
                    continue
                orders.append({"side": "buy", "mint": mint, "quote_lamports": amount,
                               "max_slippage_bps": PROBE_SLIP_BPS})
                probes[mint] = False
                probed.add(mint)
                free -= amount + tx_fee
                committed += 1

    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
