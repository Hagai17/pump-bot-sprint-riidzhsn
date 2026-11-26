# Strategy notes — Pump.fun Bot Sprint (u-riidzhsn)

Competition: `u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed`,
protocol `agent-fun/replay/v2`, harness `pumpfun-replay/v2`. The bot trades
paper money (10 SOL) on a replay of pump.fun bonding-curve events. The final
window is 6 hours of chain data recorded *after* entries close, so nothing
below is tuned to the final tape — only to the kit's published 24 h dev
window (128,058 events, 1,222 coins created, 35 completions).

## The bar

- Ranking: net PnL, highest first, among entries meeting the guardrails.
- Guardrails: a round trip of at least 0.01 SOL on each of at least 20
  coins, and max drawdown ≤ 50%.
- A prize needs net PnL > 0 SOL after every fee.
- Market rules that shape everything: orders fill 2 slots after the
  decision at the then-current reserves; per reply, buys on one coin share
  a cap of 1% of its real SOL reserves; at most 5 open positions; curve
  fees are 95 + 30 bps on buys and sells; every filled/failed order also
  pays a 0.000105 SOL tx fee; ~2% of orders fail at random; an order whose
  fill price moved past its slippage is rejected *for free* (no tx fee);
  positions are force-closed at the final reserves when a curve completes
  (exactly at 85 SOL real reserves on this tape) and flattened at the end.

## Dev-window grades of the field (kit grade-local, process driver)

| bot | net PnL | guardrails | max DD |
| --- | --- | --- | --- |
| hold cash | 0 | NOT met (no round trips) | 0 |
| equal-weight baseline | −0.019 SOL | NOT met (1 qualifying round trip) | 0.9% |
| house momentum pilot | −9.460 SOL | NOT met (DD 95.6%) | 95.6% |
| house reversion pilot | −3.887 SOL | met | 48.2% |
| house sniper pilot | −0.468 SOL | met | 5.2% |
| **this bot (final)** | **+0.00246 SOL** | **met (27 coins, 33 round trips)** | **0.34%** |

This tape bleeds every aggressive style through fees and adverse
selection; the design goal was therefore capital preservation with a
small positive edge, not maximum action.

## The bot (two sleeves)

**Probe sleeve — the guardrail guarantee.** Tiny round trips of 0.011 SOL
(just over the 0.01 bar) on liquid (≥ 3 SOL real reserves), not-crashing
curves (15-slot net flow ≥ 0, price within −2%..+10%), sold whole on the
next batch after the buy lands. Paced by block time over ~90 minutes
until 22 coins have round-tripped. Measured cost ≈ 0.0005 SOL per probe
(curve fees + 2 tx fees), ≈ −0.010 SOL total — the price of the bar.

**Main sleeve — selective momentum.** Up to 4 positions of 0.10 SOL, no
adds, one entry per coin ever. A curve qualifies only if:

- age ≥ 250 slots, real reserves 2.5–75 SOL;
- last 40 slots: net inflow ≥ 1.2 SOL, ≥ 5 buy trades, ≥ 5 distinct
  buyers, buy share of flow ≥ 65%;
- price +8%..+120% over the window and ≥ 97% of the window high;
- inflow accelerating (newer half ≥ older half) and still live
  (last-10-slot net flow ≥ +0.3 SOL);
- the coin's creator has not been seen selling (rug filter).

Exits are mechanical, sells at 10000 bps slippage: −14% value stop;
trailing stop armed at +20%, firing 18% under the peak value; hard take
profit at +60%; net flow over 20 slots < −0.8 SOL; a single recorded sell
≥ 1.5 SOL; the creator sells; or 900 slots held (waived while up ≥ 10%).
Buys carry 800 bps slippage, so a coin that runs away inside the 2-slot
latency is refused for free instead of chased. An equity brake (marked to
close value) stops new main entries after a 10% drawdown, main entries
halve after 5 h of block time and stop after 5.5 h so the end-of-window
flatten rarely catches a fresh position (the final window is 6 h).

## Iteration record (dev window, kit grade-local)

| version | change | net PnL | fills / coins / trips | max DD |
| --- | --- | --- | --- | --- |
| v1 | probes + momentum (0.06 SOL, adds, TP +60%) | −0.0332 | 95 / 31 / 45 | 0.51% |
| v2 | no adds, stake 0.08, tighter entries, TP +35%, tighter probes | −0.0415 | 80 / 29 / 40 | 0.67% |
| v3 | graduation ride: buy the run to the 85 SOL completion | −0.0910 | 60 / 25 / 30 | 1.06% |
| v4 | v1 entry levels + live-push check + creator-sell filter/exit, stake 0.06 | −0.0009 | 70 / 29 / 35 | 0.22% |
| v5 | stake 0.10, exposure 0.30, probe target 22 | +0.0020 | 66 / 27 / 33 | 0.34% |
| v6 (final) | entries slightly looser (net ≥ 1.2, ≥ 5 buyers), 4 slots, exposure 0.40 | **+0.00246** | 66 / 27 / 33 | 0.34% |

Lessons the tape taught, in order:

1. **Adds amplify losers.** v1's pyramid adds turned −5% positions into
   −8% ones; removing them was the single biggest structural fix.
2. **The graduation ride does not work as the only sleeve.** 46% of
   coins reaching 40 SOL complete, but entries at 38+ SOL stall for
   thousands of slots, stops and fade exits fire first, and one rug
   through the stop cost −0.056 SOL — more than all its wins combined.
3. **The creator filter is real edge.** v4's jump from −0.033 gross
   territory to break-even came from the push-liveness check plus
   refusing coins whose creator has sold, and exiting when he does.
4. **Fees are the opponent.** Round-trip cost is ≈ 2.5% curve fees +
   0.00021 SOL tx fees; every design choice above is downstream of
   minimising the number of times that toll is paid per unit of edge.

## Robustness

- Determinism: the grader's two executions of the final bot reproduced
  the action log byte for byte (no clock, no environment, no randomness;
  iteration only over insertion-ordered dicts).
- Stress: `grade-local --stress 6` on the v5 settings printed copies of
  +0.05, −0.07, +0.02, −0.04, −0.05, −0.02 SOL (median −0.04; 4 of 6 lost
  money). The block-spliced copies scramble block time, which the bot's
  pacing uses, so they read pessimistic; the honest summary is that the
  edge is thin and tape-dependent, centred near zero — still far above
  every house bot's dev result, with drawdown two orders of magnitude
  inside the guardrail.
- At 2× fees the dev grade is −0.039 SOL: the edge does not survive a
  fee doubling, which is exactly why position count and probe count are
  kept at the minimum the guardrails allow.

## Files

- `main.py` — the bot (entrypoint).
- `vendor/makefun_bot.py` — the kit's Python protocol shim, vendored
  (kept out of the authorship counts, as the kit instructs).
