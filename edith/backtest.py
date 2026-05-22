"""
Event-driven daily backtest engine for short-term Korean stock strategies.

Convention:
- Strategy returns BUY signal at day t (using bars up to and including t close).
- Entry happens at day t+1 OPEN (realistic for retail).
- Exit reasons:
    * stop_loss (intraday low <= stop)
    * take_profit (intraday high >= target)
    * time_exit (held for max_hold days)
    * trail (optional ATR trailing stop)
- Costs:
    * buy_fee:  0.015%
    * sell_fee: 0.015%
    * sell_tax: 0.18% (KOSPI/KOSDAQ securities transaction tax, 2025+)
    * slippage: 0.10% on each side (entry + exit)
- Position sizing: equal weight across up to `max_positions`.
- Capital pool: cash + open positions tracked daily for equity curve.

This is intentionally compact, not a full event simulator. It is realistic enough
to rank strategies against each other under matching cost assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

BUY_FEE = 0.00015
SELL_FEE = 0.00015
SELL_TAX = 0.0018
SLIPPAGE = 0.0010  # per side

ROUND_TRIP_COST = BUY_FEE + SELL_FEE + SELL_TAX + 2 * SLIPPAGE  # ~0.42% all-in


# ---------------------------------------------------------------------------
# Strategy signature
# ---------------------------------------------------------------------------
# A strategy is a callable: signal_fn(code, df) -> pd.DataFrame indexed like df
# with columns at minimum:
#   - entry (bool): True = generate entry on next bar open
#   - stop_pct (float, optional): stop-loss as fraction below entry (e.g. 0.05 = -5%)
#   - target_pct (float, optional): take-profit fraction above entry
#   - max_hold (int, optional): max bars to hold before time exit
# Missing columns fall back to engine defaults.

SignalFn = Callable[[str, pd.DataFrame], pd.DataFrame]


@dataclass
class EngineConfig:
    initial_cash: float = 10_000_000.0  # 10M KRW notional
    max_positions: int = 5
    default_stop_pct: float = 0.05      # 5% stop
    default_target_pct: float = 0.10    # 10% target
    default_max_hold: int = 5
    cooldown_days: int = 1              # days to wait after exit before re-entry on same name
    rank_key: str = "score"             # column to rank simultaneous signals by; falls back to volume
    regime: Optional[pd.Series] = None  # bool series, True = allow new entries; index = date


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class _OpenPos:
    code: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    stop: float
    target: float
    max_hold: int
    bars_held: int = 0


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: pd.DataFrame
    daily_positions: pd.DataFrame = field(default_factory=pd.DataFrame)


def _build_signals(
    data: dict[str, pd.DataFrame],
    signal_fn: SignalFn,
    cfg: EngineConfig,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for code, df in data.items():
        if df.empty or len(df) < 30:
            continue
        sig = signal_fn(code, df)
        if sig is None or sig.empty:
            continue
        # Fill defaults
        if "stop_pct" not in sig.columns:
            sig["stop_pct"] = cfg.default_stop_pct
        if "target_pct" not in sig.columns:
            sig["target_pct"] = cfg.default_target_pct
        if "max_hold" not in sig.columns:
            sig["max_hold"] = cfg.default_max_hold
        if "score" not in sig.columns:
            sig["score"] = df["Volume"].fillna(0).astype(float).reindex(sig.index).fillna(0)
        out[code] = sig
    return out


def run_backtest(
    data: dict[str, pd.DataFrame],
    signal_fn: SignalFn,
    cfg: EngineConfig | None = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> BacktestResult:
    cfg = cfg or EngineConfig()

    # Universe of all dates that appear in any frame
    all_dates: set[pd.Timestamp] = set()
    for df in data.values():
        all_dates.update(df.index.tolist())
    dates = sorted(all_dates)
    if start:
        dates = [d for d in dates if d >= pd.Timestamp(start)]
    if end:
        dates = [d for d in dates if d <= pd.Timestamp(end)]
    if not dates:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame())

    signals = _build_signals(data, signal_fn, cfg)

    cash = cfg.initial_cash
    open_positions: dict[str, _OpenPos] = {}
    last_exit_date: dict[str, pd.Timestamp] = {}
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    # Pre-compute index lookups
    for code in data:
        data[code] = data[code].sort_index()

    for i, today in enumerate(dates):
        # ------------- Mark-to-market & check exits at bar `today` -------------
        to_close: list[str] = []
        for code, pos in open_positions.items():
            df = data[code]
            if today not in df.index:
                continue
            row = df.loc[today]
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])
            pos.bars_held += 1

            exit_price: float | None = None
            reason = ""
            # Stop loss first (conservative ordering)
            if low <= pos.stop:
                exit_price = pos.stop
                reason = "stop"
            elif high >= pos.target:
                exit_price = pos.target
                reason = "target"
            elif pos.bars_held >= pos.max_hold:
                exit_price = close
                reason = "time"

            if exit_price is not None:
                # Apply exit slippage + sell fees + tax
                exit_eff = exit_price * (1 - SLIPPAGE)
                proceeds = pos.shares * exit_eff * (1 - SELL_FEE - SELL_TAX)
                cost_basis = pos.shares * pos.entry_price * (1 + SLIPPAGE) * (1 + BUY_FEE)
                pnl = proceeds - cost_basis
                pnl_pct = (proceeds / cost_basis) - 1.0 if cost_basis > 0 else 0.0
                cash += proceeds
                trades.append({
                    "code": code,
                    "entry_date": pos.entry_date,
                    "exit_date": today,
                    "entry_price": pos.entry_price,
                    "exit_price": exit_price,
                    "shares": pos.shares,
                    "pnl_krw": pnl,
                    "pnl_pct": pnl_pct,
                    "hold_days": pos.bars_held,
                    "reason": reason,
                })
                to_close.append(code)
                last_exit_date[code] = today
        for code in to_close:
            del open_positions[code]

        # ------------- Generate new entries from yesterday's signal -------------
        # Entries fire on `today`'s open, based on signal at the bar that closed
        # the day before. We look back to dates[i-1].
        if i == 0:
            equity_curve.append((today, _equity(cash, open_positions, data, today)))
            continue

        prev = dates[i - 1]
        # Regime gate: skip new entries on days where regime is False
        if cfg.regime is not None:
            try:
                if not bool(cfg.regime.loc[prev]):
                    equity_curve.append((today, _equity(cash, open_positions, data, today)))
                    continue
            except KeyError:
                # No regime reading for this date -> assume neutral, skip
                equity_curve.append((today, _equity(cash, open_positions, data, today)))
                continue
        candidates: list[tuple[float, str, pd.Series]] = []
        for code, sig in signals.items():
            if prev not in sig.index:
                continue
            row = sig.loc[prev]
            if not bool(row.get("entry", False)):
                continue
            if code in open_positions:
                continue
            cd = last_exit_date.get(code)
            if cd is not None and (today - cd).days < cfg.cooldown_days:
                continue
            df = data[code]
            if today not in df.index:
                continue
            candidates.append((float(row.get("score", 0.0)), code, row))

        # Rank by score desc and fill empty slots
        candidates.sort(key=lambda x: -x[0])
        slots = cfg.max_positions - len(open_positions)
        if slots > 0 and candidates:
            per_slot_cash = cash / slots
            for _, code, row in candidates[:slots]:
                df = data[code]
                entry_open = float(df.loc[today, "Open"])
                entry_eff = entry_open * (1 + SLIPPAGE)
                gross_per_share = entry_eff * (1 + BUY_FEE)
                if gross_per_share <= 0:
                    continue
                shares = int(per_slot_cash // gross_per_share)
                if shares <= 0:
                    continue
                cost = shares * gross_per_share
                cash -= cost
                stop_pct = float(row.get("stop_pct", cfg.default_stop_pct))
                target_pct = float(row.get("target_pct", cfg.default_target_pct))
                max_hold = int(row.get("max_hold", cfg.default_max_hold))
                open_positions[code] = _OpenPos(
                    code=code,
                    entry_date=today,
                    entry_price=entry_open,
                    shares=shares,
                    stop=entry_open * (1 - stop_pct),
                    target=entry_open * (1 + target_pct),
                    max_hold=max_hold,
                )

        equity_curve.append((today, _equity(cash, open_positions, data, today)))

    eq = pd.Series(
        [v for _, v in equity_curve],
        index=pd.DatetimeIndex([d for d, _ in equity_curve]),
        name="equity",
    )
    tdf = pd.DataFrame(trades)
    return BacktestResult(equity=eq, trades=tdf)


def _equity(
    cash: float,
    positions: dict[str, _OpenPos],
    data: dict[str, pd.DataFrame],
    today: pd.Timestamp,
) -> float:
    val = cash
    for code, pos in positions.items():
        df = data[code]
        if today in df.index:
            px = float(df.loc[today, "Close"])
        else:
            px = pos.entry_price
        val += pos.shares * px * (1 - SLIPPAGE) * (1 - SELL_FEE - SELL_TAX)
    return val
