# akuna-mm-bot

Throwaway market-making sim, built as prep for the [Akuna Capital 2026
Virtual Quant Trading Challenge](https://akunacapital.com/careers/job/7993921/akuna-capitals-2026-virtual-quant-trading-challenge/?gh_jid=7993921)
(Aug 17-21, 2026). Doubles as prep-tracker credential #2.

Not Akuna's actual harness — that's unpublished until day 1 of the
challenge. This is a generic continuous-double-auction sim: a true
value random-walks, noise flow trades passively, informed flow picks
off stale quotes. Two bots run on the same price path so their P&L and
inventory risk are directly comparable.

```bash
python3 market_maker.py
```

Prints both bots' final P&L / max inventory and runs an assert-based
self-test (kill-switch holds, sim is adversarial enough to matter,
risk-bounded bot doesn't blow up).

SkewBot's fair value is a microprice-style estimate: an EWMA of trade
price tilted by recent order-flow imbalance (heavy hits on the ask pull
it up, heavy hits on the bid pull it down) rather than raw last-trade —
no real order book is visible in this sim, so this is the flow-based
fallback the prep doc names. Swap for literal book-depth microprice
once Akuna's feed exposes bid/ask sizes.

## Next (once the real spec drops Aug 17)

- Swap `TrueValuePath`/`Flow` for whatever feed/API Akuna's sim exposes.
- Keep the SkewBot structure: kill-switch and inventory skew first,
  fair-value model second.
- `attribute_pnl()` is there for the day-by-day spread-capture vs.
  inventory-P&L split from the prep checklist.
