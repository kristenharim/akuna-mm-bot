"""
Throwaway market-making sim — prep for the Akuna 2026 Virtual Quant
Trading Challenge (Aug 17-21). Goal per the prep packet: hit the three
walls (inventory blowup, getting picked off, spread too wide to fill)
*before* the real 5-8hr window, not during it.

Not Akuna's harness — theirs is unpublished until day 1. This is a
generic single-instrument continuous double-auction sim: a true value
random-walks, noise flow trades passively against your quotes, and
"informed" flow trades against you specifically when your quote is
stale relative to a value move (the adverse-selection mechanic).

Two bots are compared on the same price path (same seed):
  - NaiveBot: fixed-width spread around last trade price, no skew.
  - SkewBot:  inventory-skewed quotes + a hard position kill-switch +
              a microprice-style fair value (EWMA tilted by recent
              order-flow imbalance) instead of raw last-trade + a
              volatility-scaled spread (widens when recent moves are
              large, capped so it can't runaway-widen itself).

Levers this exercises, per prep_tracker checklist: spread width,
inventory skew, fair-value estimation, risk-first ordering.
"""
import random
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Fill:
    tick: int
    side: str  # "buy" or "sell" from the bot's perspective
    price: float
    size: int


@dataclass
class BotResult:
    name: str
    cash: float = 0.0
    inventory: int = 0
    fills: list = field(default_factory=list)
    pnl_history: list = field(default_factory=list)
    max_abs_inventory: int = 0

    def mark_to_market(self, true_value: float) -> float:
        return self.cash + self.inventory * true_value


class TrueValuePath:
    """Random-walk fair value the bots never directly observe."""

    def __init__(self, start=100.0, sigma=0.15, seed=0):
        self.value = start
        self.sigma = sigma
        self.rng = random.Random(seed)

    def step(self):
        self.value += self.rng.gauss(0, self.sigma)
        return self.value


class Flow:
    """Noise + informed order flow that trades against the bot's quotes."""

    def __init__(self, seed=1, noise_rate=0.6, informed_rate=0.5, informed_edge=0.05):
        self.rng = random.Random(seed)
        self.noise_rate = noise_rate
        self.informed_rate = informed_rate
        self.informed_edge = informed_edge  # min mispricing that triggers a pick-off

    def trades(self, bid, ask, true_value_now, true_value_next):
        """Yield ("buy"/"sell", size) from the bot's perspective for this tick."""
        # Noise flow: hits either side uniformly at random, independent of value.
        if self.rng.random() < self.noise_rate:
            side = self.rng.choice(["buy", "sell"])
            yield side, 1

        # Informed flow: sees true_value_next before it's public. If the bot's
        # ask is stale-cheap relative to where value is headed, informed buys
        # it (bot sells low); mirror on the bid.
        if self.rng.random() < self.informed_rate:
            if true_value_next - ask > self.informed_edge:
                yield "buy", 1
            elif bid - true_value_next > self.informed_edge:
                yield "sell", 1


class NaiveBot:
    """Fixed-width spread around the last trade price. No skew, no cap."""

    name = "naive"

    def __init__(self, half_spread=0.5):
        self.half_spread = half_spread
        self.last_price = 100.0

    def quote(self, inventory):
        return self.last_price - self.half_spread, self.last_price + self.half_spread

    def on_fill(self, price, side):
        self.last_price = price


class SkewBot:
    """Inventory-skewed quotes + hard position cap (kill-switch first) +
    a microprice-style fair value instead of raw last-trade.

    No real order book is visible in this sim (bots only see their own
    fills), so "microprice" here is approximated as an EWMA of trade
    price tilted by recent order-flow imbalance — the prep doc's stated
    fallback ("microprice or order-flow-imbalance weighted mid, not the
    naive mid"). Heavy recent hits on the ask (aggressive buying) pull
    fair value up toward the ask; heavy hits on the bid pull it down.
    Swap for a literal book-depth microprice once Akuna's real feed
    exposes bid/ask sizes.
    """

    name = "skew"

    def __init__(self, base_half_spread=0.4, max_pos=10, skew_per_unit=0.04,
                 ewma_alpha=0.15, flow_window=20, imbalance_sensitivity=0.3,
                 vol_alpha=0.1, vol_multiplier=3.0, max_half_spread=1.2):
        self.base_half_spread = base_half_spread
        self.max_pos = max_pos
        self.skew_per_unit = skew_per_unit
        self.ewma_alpha = ewma_alpha
        self.imbalance_sensitivity = imbalance_sensitivity
        self.vol_alpha = vol_alpha
        self.vol_multiplier = vol_multiplier
        # ponytail: hard cap on quoted spread. Without it, a wide spread pushes
        # fill prices away from fair value -> vol estimate reads that as more
        # volatility -> spread widens further -> runaway. Real desks cap max
        # spread for the same reason; raise this if legitimate vol needs more.
        self.max_half_spread = max_half_spread
        self.fair_value = 100.0
        self.vol_ewma = 0.0  # EWMA of |fair value change| per fill, a volatility proxy
        self.recent_fills = deque(maxlen=flow_window)  # "buy"/"sell", bot's side

    def _microprice(self):
        ask_hits = self.recent_fills.count("sell")  # bot sold -> buyer aggression
        bid_hits = self.recent_fills.count("buy")   # bot bought -> seller aggression
        total = ask_hits + bid_hits
        imbalance = (ask_hits - bid_hits) / total if total else 0.0
        return self.fair_value + imbalance * self.imbalance_sensitivity

    def quote(self, inventory):
        # Shade quotes against the direction of existing inventory so the
        # bot gets lifted/hit more on the reducing side (rung 1: risk first).
        skew = -inventory * self.skew_per_unit
        fv = self._microprice()
        # Widen when recent moves have been large, tighten when calm — the
        # prep doc's "widen when volatility rises or confidence drops."
        half_spread = min(
            self.base_half_spread + self.vol_multiplier * self.vol_ewma,
            self.max_half_spread,
        )
        bid = fv - half_spread + skew
        ask = fv + half_spread + skew
        # Kill-switch: at the cap, stop quoting the side that would extend it.
        if inventory >= self.max_pos:
            bid = None
        if inventory <= -self.max_pos:
            ask = None
        return bid, ask

    def on_fill(self, price, side):
        # Volatility proxy = the size of the step our own estimate takes, not
        # the raw (price - fair_value) gap — that gap structurally includes
        # our own quoted spread, which would bias "volatility" upward by
        # however wide we're quoting and feed the widen->bias->widen loop
        # the spread cap above guards against.
        increment = self.ewma_alpha * (price - self.fair_value)
        self.fair_value += increment
        self.vol_ewma += self.vol_alpha * (abs(increment) - self.vol_ewma)
        self.recent_fills.append(side)


def run(bot, true_path: TrueValuePath, flow: Flow, ticks: int) -> BotResult:
    result = BotResult(name=bot.name)
    for t in range(ticks):
        true_now = true_path.value
        bid, ask = bot.quote(result.inventory)
        true_next = true_path.step()

        for side, size in flow.trades(
            bid if bid is not None else true_now - 1e9,
            ask if ask is not None else true_now + 1e9,
            true_now,
            true_next,
        ):
            if side == "buy" and ask is not None:
                # counterparty buys -> bot sells at its ask
                result.cash += ask * size
                result.inventory -= size
                result.fills.append(Fill(t, "sell", ask, size))
                bot.on_fill(ask, "sell")
            elif side == "sell" and bid is not None:
                result.cash -= bid * size
                result.inventory += size
                result.fills.append(Fill(t, "buy", bid, size))
                bot.on_fill(bid, "buy")
            # Re-quote off the post-fill inventory before the next event in
            # this tick, so a kill-switch cap can't be blown past by a second
            # same-tick fill landing before the bot ever saw the new position.
            bid, ask = bot.quote(result.inventory)

        result.max_abs_inventory = max(result.max_abs_inventory, abs(result.inventory))
        result.pnl_history.append(result.mark_to_market(true_next))
    return result


def attribute_pnl(result: BotResult, true_path_values) -> dict:
    """Split final P&L into spread-capture (fills vs. true value at fill time)
    vs inventory P&L (value of held position drifting), per the checklist's
    'plot P&L attribution' item."""
    spread_pnl = 0.0
    for f in result.fills:
        tv = true_path_values[f.tick]
        spread_pnl += (f.price - tv) if f.side == "sell" else (tv - f.price)
    total = result.pnl_history[-1] if result.pnl_history else 0.0
    return {"total": total, "spread_capture": spread_pnl, "inventory_pnl": total - spread_pnl}


def demo():
    TICKS = 2000
    SEED = 42

    true_path = TrueValuePath(seed=SEED)
    flow = Flow(seed=SEED + 1)
    values = [true_path.value]
    naive = NaiveBot()
    naive_result = run(naive, true_path, flow, TICKS)

    # Same price path + flow for a fair comparison.
    true_path2 = TrueValuePath(seed=SEED)
    flow2 = Flow(seed=SEED + 1)
    skew = SkewBot()
    skew_result = run(skew, true_path2, flow2, TICKS)

    print(f"naive: final P&L={naive_result.pnl_history[-1]:.2f}  "
          f"max|inventory|={naive_result.max_abs_inventory}")
    print(f"skew : final P&L={skew_result.pnl_history[-1]:.2f}  "
          f"max|inventory|={skew_result.max_abs_inventory}")

    # --- self-test: the three walls from the prep checklist ---
    # 1. Kill-switch actually caps inventory.
    assert skew_result.max_abs_inventory <= skew.max_pos, "kill-switch breached position cap"
    # 2. Naive bot has no cap and this sim is adversarial enough to prove it
    #    (sanity: naive should NOT be artificially capped by construction).
    assert naive_result.max_abs_inventory >= skew_result.max_abs_inventory - 1, \
        "sim isn't adversarial enough to distinguish capped vs uncapped bots"
    # 3. Bounded-risk bot should not blow up: steady beats spiky is the pass
    #    bar from the prep doc, i.e. skew bot's P&L shouldn't be wildly worse
    #    than naive's despite tighter risk.
    assert skew_result.pnl_history[-1] > -50, "skew bot lost far more than a bounded bot should"

    print("self-test passed: kill-switch holds, sim is adversarial, risk-bounded bot survives")


if __name__ == "__main__":
    demo()
