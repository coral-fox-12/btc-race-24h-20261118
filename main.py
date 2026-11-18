"""btc-race-24h-20261118-1444: a 60-minute momentum bot for cbBTC/USDC, long only.

The idea, in one paragraph: one-minute bars are mostly noise (about 7 bps of
per-minute standard deviation against a ~26 bps round-trip cost), so the bot
ignores everything below the one-hour scale. It watches the trailing 60-bar
return of the close. When that return has been above +50 bps for three bars
in a row, the market has a real one-hour uptrend, and the bot buys with its
full cash. It sells when the 60-bar return turns below -20 bps, when the
position is 1.5% over its cost, or when it is 1.0% under it. After any exit
it waits until the 60-bar return has fallen back to +50 bps or below before
it may enter again, so a take-profit exit is never followed by an immediate
re-buy at the top of the same move.

All decisions use exact integer comparisons on the closes the harness sends;
there is no randomness, no clock and no environment read, so two runs of the
same bars give the same orders byte for byte. The bot speaks
agent-fun/bars-replay/v1 through the kit's Python shim, vendored under
vendor/ byte for byte.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Strategy parameters (in basis points; 100 bps = 1%).
LOOKBACK_BARS = 60        # the momentum window: one hour of one-minute bars
ENTER_BPS = 50            # enter when the trailing 60-bar return tops this
CONFIRM_BARS = 3          # ... for this many bars in a row
EXIT_BPS = -20            # exit when the trailing 60-bar return falls below this
TAKE_PROFIT_BPS = 150     # exit when the position is this far over its cost
STOP_LOSS_BPS = 100       # exit when the position is this far under its cost
MAX_SLIPPAGE_BPS = 100    # cap every order 1% past the close it was decided on

# The closes of every CBBTC bar the harness has sent, in order.
_closes = []
# Consecutive bars the entry signal has held.
_confirm = 0
# False after an exit until the entry signal has reset (fallen back to the
# entry threshold or below); stops an exit being followed by a re-buy while
# the same move is still running.
_armed = True


def _trailing_return():
    """The closes the trailing 60-bar return is computed from, or None.

    Returns the pair (now, then): the latest close and the close 60 bars
    before it. Callers compare them in exact integer form,
    (now - then) * 10_000 against a threshold times `then`, so no division
    ever happens and the sign of the comparison is exact. None until 61
    closes have been seen, i.e. until the first bar the return is defined on.
    """
    if len(_closes) <= LOOKBACK_BARS:
        return None
    return _closes[-1], _closes[-1 - LOOKBACK_BARS]


def _above(now, then, bps):
    return (now - then) * 10_000 > bps * then


def _below(now, then, bps):
    return (now - then) * 10_000 < bps * then


def on_batch(state, batch):
    global _confirm, _armed
    # Only a batch that carries a fresh CBBTC bar advances the history; a
    # batch without one (a gap minute) adds nothing.
    if any(b.get("symbol") == SYMBOL for b in batch.get("bars", [])):
        close = state.close(SYMBOL)
        if close is not None:
            _closes.append(close)

    ret = _trailing_return()
    held = state.positions.get(SYMBOL)

    if held is not None:
        # Holding: the exits, in order. The momentum exit first, then the
        # take-profit and the stop-loss on the position's value at this close.
        if ret is not None and _below(ret[0], ret[1], EXIT_BPS):
            _armed = False
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        # The stop-loss never fired on the dev window, and it is kept anyway:
        # it is insurance for the unseen race window, capping any single
        # trade entered just before a sharp drop at about -1% plus costs.
        value = state.value(SYMBOL, held.qty)
        cost = held.cost
        if cost > 0:
            if value * 10_000 >= cost * (10_000 + TAKE_PROFIT_BPS):
                _armed = False
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            if value * 10_000 <= cost * (10_000 - STOP_LOSS_BPS):
                _armed = False
                return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # Flat: track the entry signal and the re-entry reset.
    if ret is None:
        _confirm = 0
        return []
    now, then = ret
    if _above(now, then, ENTER_BPS):
        _confirm += 1
    else:
        _confirm = 0
        _armed = True
    if not _armed:
        return []
    if _confirm >= CONFIRM_BARS and state.cash > 0:
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": state.cash,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
