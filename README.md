# Pump Bot Sprint — Riidzhsn

[![make.fun bounty](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/c/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=pump-bot-sprint-riidzhsn)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/Hagai17/pump-bot-sprint-riidzhsn/rank.svg)](https://make.fun/c/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=pump-bot-sprint-riidzhsn)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/Hagai17/pump-bot-sprint-riidzhsn/reward.svg)](https://make.fun/c/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=pump-bot-sprint-riidzhsn)

Entry for the make.fun bounty [Pump.fun Bot Sprint: Make Bot And Win](https://make.fun/c/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=pump-bot-sprint-riidzhsn).

A paper-trading bot for the `agent-fun/replay/v2` pump.fun bonding-curve replay.
It runs two sleeves:

- **Probe sleeve** — guarantees the guardrail bar (round trips of at least
  0.01 SOL on 20+ coins, drawdown well under 50%): tiny 0.011 SOL round trips
  on liquid, not-crashing curves, paced over the first ~90 minutes.
- **Main sleeve** — selective momentum: at most 3 positions of 0.06 SOL on
  mature curves with strong, broad, accelerating net inflow and the price at
  its window high; mechanical exits (stop, trailing stop, take profit, flow
  fade, time limit), an equity drawdown brake, and slippage settings that
  refuse to chase a coin that runs away inside the order latency.

Deterministic by construction: no clock, no environment, no randomness.

## Layout

- `main.py` — the bot (entrypoint; the grader runs `python3 -P -s main.py`).
- `vendor/makefun_bot.py` — the kit's Python protocol shim, vendored.
- `candidates.md` — strategy design notes and local grade results.

## License

MIT — see `LICENSE`.
