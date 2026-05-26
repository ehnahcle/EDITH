"""
EDITH Dashboard
===============
Streamlit web UI for the EDITH KR short-term trading toolkit.

Runs locally (`streamlit run dashboard.py`) AND on Streamlit Community Cloud
(mobile-friendly, optional password lock via st.secrets).
"""

from __future__ import annotations

import glob
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from edith.final_strategy import (
    final_strategy, FINAL_PARAMS_MOMENTUM,
    PARAMS_STRONG_BULL, PARAMS_WEAK, PARAMS_BEAR,
)
from edith.metrics import summarize


def _lazy_kospi_regime(start: str, end: str) -> pd.Series:
    """Lazy import so Streamlit Cloud doesn't crash when KRX libs are absent."""
    try:
        from edith.regime import kospi_regime
        return kospi_regime(start, end)
    except Exception:
        return pd.Series(dtype=bool)


# Backward-compatible name used elsewhere in this file
kospi_regime = _lazy_kospi_regime


RESULTS_DIR = ROOT / "results"
N_KOSPI = 100
N_KOSDAQ = 50
LOOKBACK_DAYS = 400

# Detect Streamlit Community Cloud — disables expensive operations there
IS_CLOUD = os.environ.get("STREAMLIT_RUNTIME") == "cloud" or os.environ.get("HOSTNAME", "").startswith("streamlit-")


# ============================================================
# Page config
# ============================================================
st.set_page_config(
    page_title="EDITH",
    page_icon="🦸",
    layout="centered",  # Works better on phones than 'wide'
    initial_sidebar_state="collapsed",  # Auto-hide sidebar on mobile
)

# Compact mobile CSS
st.markdown("""
<style>
  /* Reduce top padding so header fits on phone screens */
  .main .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1000px; }
  /* Smaller metric labels on mobile */
  [data-testid="stMetricLabel"] { font-size: 0.85rem; }
  [data-testid="stMetricValue"] { font-size: 1.4rem; }
  /* Make tables scroll horizontally on small screens */
  [data-testid="stDataFrame"] { overflow-x: auto; }
  /* Tighter dividers */
  hr { margin: 1rem 0; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# Optional password gate (set in .streamlit/secrets.toml or Cloud secrets)
# ============================================================
def check_password() -> bool:
    """Return True if no password configured, or user typed correct one."""
    try:
        expected = st.secrets.get("password", "")
    except Exception:
        expected = ""
    if not expected:
        return True  # no password configured -> open access

    if st.session_state.get("authed"):
        return True

    st.title("🦸 EDITH")
    st.caption("KR Short-term Trading Dashboard")
    pwd = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
    if not pwd:
        st.stop()
    if pwd == expected:
        st.session_state["authed"] = True
        st.rerun()
    else:
        st.error("비밀번호가 틀렸습니다.")
        st.stop()
    return False


check_password()


# ============================================================
# Data fetch (only meaningful when running locally with KRX access)
# ============================================================

def _try_import_fdr():
    """Lazy import so Cloud deploys without KRX access don't crash at startup."""
    try:
        from edith.data_loader import get_top_universe, get_ohlcv_batch, get_index
        return get_top_universe, get_ohlcv_batch, get_index
    except Exception:
        return None, None, None


# ============================================================
# Cached loaders
# ============================================================

@st.cache_data(ttl=60 * 15)
def load_final_equity() -> dict[str, pd.Series]:
    out = {}
    # New live strategy: Triple-mode + WEAK foreign booster (B_BoostWEAK_w0.5).
    # Triple_full = same dispatcher without the foreign booster (apples-to-apples).
    live_curves = [
        ("Triple+Booster (LIVE)", "booster_equity_B_BoostWEAK_w0.5.csv"),
        ("Triple_full (no booster)", "triple_equity_Triple_full.csv"),
    ]
    for label, fname in live_curves:
        p = RESULTS_DIR / fname
        if p.exists():
            s = pd.read_csv(p, index_col=0, parse_dates=True)["equity"]
            out[label] = s
    # Legacy single-strategy curves (2020-2026 only — strong-bull-biased sample).
    for name in ["Momentum5_tuned", "NewHigh52w_tuned", "Ensemble_M+NH"]:
        p = RESULTS_DIR / f"final_equity_{name}.csv"
        if p.exists():
            s = pd.read_csv(p, index_col=0, parse_dates=True)["equity"]
            out[f"Legacy: {name}"] = s
    # KOSPI buy & hold benchmark, normalised to 10M (skip on Cloud)
    if not IS_CLOUD:
        _, _, get_index = _try_import_fdr()
        if get_index is not None:
            try:
                idx = get_index("KS11", "2020-01-01", datetime.today().strftime("%Y-%m-%d"))
                if not idx.empty:
                    bh = idx["Close"] / idx["Close"].iloc[0] * 10_000_000.0
                    out["KOSPI B&H"] = bh
            except Exception:
                pass
    # On cloud or fetch failure, look for a pre-committed KOSPI benchmark CSV
    if "KOSPI B&H" not in out:
        p_bh = RESULTS_DIR / "kospi_buyhold.csv"
        if p_bh.exists():
            out["KOSPI B&H"] = pd.read_csv(p_bh, index_col=0, parse_dates=True)["equity"]
    return out


@st.cache_data(ttl=60 * 15)
def load_final_trades() -> dict[str, pd.DataFrame]:
    out = {}
    live_trades = [
        ("Triple+Booster (LIVE)", "booster_trades_B_BoostWEAK_w0.5.csv"),
        ("Triple_full (no booster)", "triple_trades_Triple_full.csv"),
    ]
    for label, fname in live_trades:
        p = RESULTS_DIR / fname
        if p.exists():
            out[label] = pd.read_csv(p, parse_dates=["entry_date", "exit_date"])
    for name in ["Momentum5_tuned", "NewHigh52w_tuned", "Ensemble_M+NH"]:
        p = RESULTS_DIR / f"final_trades_{name}.csv"
        if p.exists():
            df = pd.read_csv(p, parse_dates=["entry_date", "exit_date"])
            out[f"Legacy: {name}"] = df
    return out


@st.cache_data(ttl=60 * 15)
def load_final_summary() -> pd.DataFrame:
    """Live booster summary (long-format) reshaped to wide for the Performance
    table. Includes Legacy 2020-2026 rows for comparison."""
    rows = []
    p_b = RESULTS_DIR / "foreign_booster_summary.csv"
    if p_b.exists():
        df = pd.read_csv(p_b)
        # period column has format "B_BoostWEAK_w0.5 FULL  2010-2024"
        for _, r in df.iterrows():
            tag = str(r["period"]).strip()
            # Keep only FULL rows (single-line view); IS/OOS shown separately later.
            if " FULL " in tag:
                strategy_part = tag.split(" FULL ")[0].strip()
                if strategy_part.startswith("B_BoostWEAK_w0.5"):
                    label = "Triple+Booster (LIVE)"
                elif strategy_part.startswith("A_Baseline"):
                    label = "Triple_full (no booster)"
                elif strategy_part.startswith("KOSPI_BH"):
                    label = "KOSPI B&H (2010-2024)"
                else:
                    continue
                rows.append({
                    "Strategy": label,
                    "CAGR": r["CAGR"],
                    "Sharpe": r["Sharpe"],
                    "Sortino": r["Sortino"],
                    "MDD": r["MDD"],
                    "Final": r["Final"],
                    "N_trades": r["N_trades"],
                    "WinRate": r["WinRate"],
                    "ProfitFactor": r["ProfitFactor"],
                })
    # Append legacy rows for comparison (different sample window).
    p_old = RESULTS_DIR / "final_summary.csv"
    if p_old.exists():
        df_old = pd.read_csv(p_old)
        df_old["Strategy"] = "Legacy: " + df_old["Strategy"].astype(str) + " (2020-26)"
        cols_keep = ["Strategy", "CAGR", "Sharpe", "Sortino", "MDD", "Final",
                     "N_trades", "WinRate", "ProfitFactor"]
        for c in cols_keep:
            if c not in df_old.columns:
                df_old[c] = None
        rows.extend(df_old[cols_keep].to_dict("records"))
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("Strategy")


def _load_latest_cached_signals() -> tuple[pd.DataFrame, str | None, datetime | None]:
    """Read the most recently committed signals_YYYY-MM-DD.csv from results/.
    Returns (df, filename, mtime) or (empty_df, None, None)."""
    files = sorted(glob.glob(str(RESULTS_DIR / "signals_*.csv")), reverse=True)
    if not files:
        return pd.DataFrame(), None, None
    latest = files[0]
    df = pd.read_csv(latest, dtype={"code": str})
    mtime = datetime.fromtimestamp(Path(latest).stat().st_mtime)
    return df, Path(latest).name, mtime


def _load_cached_regime_series() -> pd.Series:
    """Legacy 2-tier regime (bool) for backward compatibility."""
    p_reg = RESULTS_DIR / "regime_series.csv"
    if p_reg.exists():
        s = pd.read_csv(p_reg, index_col=0, parse_dates=True)["regime"].astype(bool)
        return s
    return pd.Series(dtype=bool)


def _load_cached_regime3_series() -> pd.Series:
    """3-tier regime (STRONG_BULL / WEAK / BEAR) for Triple-mode dispatcher."""
    p_reg = RESULTS_DIR / "regime3_series.csv"
    if p_reg.exists():
        s = pd.read_csv(p_reg, index_col=0, parse_dates=True)["regime3"].astype(str)
        return s
    return pd.Series(dtype=object)


REGIME3_INFO = {
    "STRONG_BULL": {
        "color": "#22c55e",
        "emoji": "🟢",
        "label": "STRONG_BULL",
        "korean": "강한 추세장",
        "strategy": "Momentum5",
        "rule": "5일 +10% 모멘텀 매수",
        "stop": "−3%",
        "target": "+15%",
        "hold": "7거래일",
    },
    "WEAK": {
        "color": "#f59e0b",
        "emoji": "🟡",
        "label": "WEAK",
        "korean": "박스권 / 약한 추세장",
        "strategy": "NewHigh52w",
        "rule": "52주 신고가 돌파 매수 (메인 알파)",
        "stop": "−7%",
        "target": "+20%",
        "hold": "10거래일",
    },
    "BEAR": {
        "color": "#ef4444",
        "emoji": "🔴",
        "label": "BEAR",
        "korean": "약세장",
        "strategy": "Disparity",
        "rule": "이격도 -10% 단기 반등 매수 (방어적)",
        "stop": "−3%",
        "target": "+10%",
        "hold": "3거래일",
    },
}


@st.cache_data(ttl=60 * 5)
def compute_today_signals(capital: float, top_n: int, force: bool, allow_live: bool):
    """Returns (today_regime, regime_series, signals_df, source, data_time).

    `source` is 'live' (recomputed now) or 'cache:<fname>' (read from committed CSV).
    `data_time` is when the data was fetched (live: now / cache: file mtime).
    On Streamlit Cloud we ALWAYS prefer the cache to avoid network/IP issues.
    """
    end = datetime.today().strftime("%Y-%m-%d")
    start = (datetime.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    if allow_live and not IS_CLOUD:
        try:
            regime = kospi_regime(start, end)
            if not regime.empty:
                today_regime = bool(regime.iloc[-1])
                get_top_universe, get_ohlcv_batch, _ = _try_import_fdr()
                if get_top_universe is not None:
                    uni = get_top_universe(n_kospi=N_KOSPI, n_kosdaq=N_KOSDAQ, force=force)
                    name_map = dict(zip(uni["Code"], uni["Name"]))
                    board_map = dict(zip(uni["Code"], uni["Board"]))
                    data = get_ohlcv_batch(uni["Code"].tolist(), start, end, force=force)
                    candidates = []
                    for code, df in data.items():
                        if len(df) < 30:
                            continue
                        sig = final_strategy(code, df)
                        last = sig.iloc[-1]
                        if not bool(last.get("entry", False)):
                            continue
                        close = float(df["Close"].iloc[-1])
                        candidates.append({
                            "code": code,
                            "name": name_map.get(code, ""),
                            "board": board_map.get(code, ""),
                            "close": close,
                            "ret5d": float(last["score"]),
                            "stop_pct": float(last["stop_pct"]),
                            "target_pct": float(last["target_pct"]),
                            "max_hold": int(last["max_hold"]),
                            "signal_date": sig.index[-1],
                        })
                    live_time = datetime.now()
                    if candidates:
                        df_live = pd.DataFrame(candidates).sort_values("ret5d", ascending=False).head(top_n)
                        per_slot = capital / top_n
                        df_live["alloc_krw"] = per_slot
                        df_live["shares"] = (df_live["alloc_krw"] / df_live["close"]).astype(int)
                        df_live["stop_price"] = (df_live["close"] * (1 - df_live["stop_pct"])).round().astype(int)
                        df_live["target_price"] = (df_live["close"] * (1 + df_live["target_pct"])).round().astype(int)
                        return today_regime, regime, df_live, "live", live_time
                    return today_regime, regime, pd.DataFrame(), "live", live_time
        except Exception as e:  # noqa: BLE001
            st.info(f"실시간 데이터 수집 실패 ({type(e).__name__}). 가장 최근 캐시된 시그널을 사용합니다.")

    # --- Cache fallback path (used on Cloud or when live fails) ---
    df_cached, fname, mtime = _load_latest_cached_signals()
    regime = _load_cached_regime_series()
    today_regime = bool(regime.iloc[-1]) if not regime.empty else None
    if df_cached.empty:
        return today_regime, regime, pd.DataFrame(), "none", mtime

    # Recompute sizing using the user-specified capital + top_n
    df_cached = df_cached.head(top_n).copy()
    per_slot = capital / top_n
    df_cached["alloc_krw"] = per_slot
    if "close" in df_cached.columns:
        df_cached["shares"] = (df_cached["alloc_krw"] / df_cached["close"]).astype(int)
        df_cached["stop_price"] = (df_cached["close"] * (1 - df_cached.get("stop_pct", 0.03))).round().astype(int)
        df_cached["target_price"] = (df_cached["close"] * (1 + df_cached.get("target_pct", 0.15))).round().astype(int)
    return today_regime, regime, df_cached, f"cache:{fname}", mtime


@st.cache_data(ttl=60 * 60)
def compute_yearly_returns(equities: dict[str, pd.Series]) -> pd.DataFrame:
    rows = {}
    for name, eq in equities.items():
        yearly = eq.resample("YE").last().pct_change()
        yearly.index = yearly.index.year
        rows[name] = yearly
    return pd.DataFrame(rows)


# ============================================================
# Sidebar
# ============================================================
st.sidebar.title("🦸 EDITH")
st.sidebar.caption("Even Dead, I'm The Hero")
st.sidebar.caption("KR 단타·스윙 백테스트 툴")

page = st.sidebar.radio(
    "Page",
    ["Today's Signals", "Manual", "Performance", "Trades", "Strategy Stats", "Config"],
    index=0,
)
st.sidebar.divider()

capital = st.sidebar.number_input(
    "Capital (KRW)",
    min_value=1_000_000,
    max_value=1_000_000_000,
    value=10_000_000,
    step=1_000_000,
)
top_n = st.sidebar.slider("Max positions", min_value=3, max_value=10, value=5)
allow_live = st.sidebar.checkbox(
    "🌐 실시간 데이터 수집",
    value=not IS_CLOUD,
    help="OFF면 GitHub에 저장된 가장 최근 시그널 CSV를 보여줍니다. Streamlit Cloud에서는 자동 OFF.",
    disabled=IS_CLOUD,
)
force_refresh = st.sidebar.button("🔄 Refresh data (force)", disabled=not allow_live)

if IS_CLOUD:
    st.sidebar.info("☁️ Streamlit Cloud 모드\n실시간 KRX 접근 불가 → 캐시된 시그널만 표시")

# ============================================================
# Header
# ============================================================
hdr1, hdr2, hdr3 = st.columns([2, 1, 1])
with hdr1:
    st.title("EDITH")
    st.caption(f"KR Short-term Trading · Triple-Mode + Foreign Booster · {datetime.today().strftime('%Y-%m-%d %H:%M')}")

# Current live strategy = Triple-mode dispatcher + WEAK foreign booster (w=0.5)
# These come from results/foreign_booster_summary.csv (B_BoostWEAK_w0.5 FULL row).
LIVE_BACKTEST = {
    "label": "Triple+Booster",
    "cagr_full": 0.2060,   # 2010-2024 FULL CAGR
    "sharpe_full": 0.96,   # 2010-2024 FULL Sharpe
    "mdd_full": -0.274,    # 2010-2024 FULL MDD
    "sharpe_oos_2024": 0.86,
    "period": "2010-2024",
}
with hdr2:
    st.metric(
        f"Backtest CAGR ({LIVE_BACKTEST['period']})",
        f"{LIVE_BACKTEST['cagr_full']*100:.1f}%",
        help="Triple-Mode + WEAK Foreign Booster (w=0.5), 14년 walk-forward",
    )
with hdr3:
    st.metric(
        "Sharpe",
        f"{LIVE_BACKTEST['sharpe_full']:.2f}",
        delta=f"2024 OOS {LIVE_BACKTEST['sharpe_oos_2024']:.2f}",
        help="Full-sample Sharpe / 2024 OOS year",
    )

st.divider()


# ============================================================
# Page: Today's Signals
# ============================================================
if page == "Today's Signals":
    st.subheader("📅 Today's Entry Candidates")

    with st.spinner("Loading signals..."):
        regime_today, regime_series, sig_df, source, data_time = compute_today_signals(
            capital=capital, top_n=top_n, force=force_refresh, allow_live=allow_live
        )

    # Source + freshness badge
    now = datetime.now()
    def _fmt_age(t: datetime | None) -> str:
        if t is None:
            return ""
        delta = now - t
        sec = int(delta.total_seconds())
        if sec < 60:
            return f"{sec}초 전"
        if sec < 3600:
            return f"{sec // 60}분 전"
        if sec < 86400:
            return f"{sec // 3600}시간 전"
        return f"{sec // 86400}일 전"

    if source.startswith("cache:"):
        fname = source.split(":", 1)[1]
        if data_time is not None:
            age = _fmt_age(data_time)
            stale_emoji = "🟢" if (now - data_time).total_seconds() < 86400 else "🟡" if (now - data_time).total_seconds() < 86400 * 3 else "🔴"
            st.caption(
                f"📦 캐시된 시그널 — `{fname}` · "
                f"{stale_emoji} 데이터 가져온 시각: **{data_time.strftime('%Y-%m-%d %H:%M:%S')}** ({age})"
            )
        else:
            st.caption(f"📦 캐시된 시그널 사용 — `{fname}`")
    elif source == "live":
        if data_time is not None:
            st.caption(
                f"🟢 실시간 데이터 · 수집 시각: **{data_time.strftime('%Y-%m-%d %H:%M:%S')}** ({_fmt_age(data_time)})"
            )
        else:
            st.caption("🟢 실시간 데이터")
    elif source == "none":
        st.warning("저장된 시그널이 없습니다. 로컬에서 `edith` 명령을 실행해 시그널을 생성하고 GitHub에 push하세요.")
        st.stop()

    # ---- 3-tier regime display ----
    regime3 = _load_cached_regime3_series()
    today_reg3 = "UNKNOWN"
    if not regime3.empty:
        today_reg3 = str(regime3.iloc[-1])
    elif sig_df is not None and not sig_df.empty and "regime" in sig_df.columns:
        # signals_*.csv contains a 'regime' column for each candidate
        today_reg3 = str(sig_df["regime"].iloc[0])

    info = REGIME3_INFO.get(today_reg3, {
        "color": "#6b7280", "emoji": "⚪", "label": "UNKNOWN",
        "korean": "분류 불가", "strategy": "—",
        "rule": "—", "stop": "—", "target": "—", "hold": "—",
    })

    # Headline card
    st.markdown(
        f"<div style='padding:1rem; border-radius:0.5rem; "
        f"background:{info['color']}22; border-left:4px solid {info['color']};'>"
        f"<div style='font-size:1.3rem; font-weight:600;'>"
        f"{info['emoji']} {info['label']} — {info['korean']}</div>"
        f"<div style='margin-top:0.5rem; font-size:0.95rem; color:#a3a3a3;'>"
        f"오늘 활성 전략: <b>{info['strategy']}</b> · {info['rule']}<br>"
        f"손절 {info['stop']} / 익절 {info['target']} / 최대보유 {info['hold']}"
        f"</div></div>",
        unsafe_allow_html=True,
    )
    st.write("")

    # Metric row
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Capital / slot", f"{capital/top_n:,.0f} KRW")
    with c2:
        if not regime3.empty:
            last_60 = regime3.iloc[-60:]
            bull_d = int((last_60 == "STRONG_BULL").sum())
            weak_d = int((last_60 == "WEAK").sum())
            bear_d = int((last_60 == "BEAR").sum())
            st.metric("최근 60일 분포",
                      f"🟢{bull_d}  🟡{weak_d}  🔴{bear_d}")
    with c3:
        # Backward-compat legacy bullish flag if we ended up here without regime3
        if not regime3.empty:
            bull_today = today_reg3 == "STRONG_BULL"
            st.metric("거래 가능?",
                      "예 (적극)" if bull_today else
                      "예 (제한)" if today_reg3 != "BEAR" else
                      "방어적")

    # 3-tier regime ribbon
    if not regime3.empty:
        st.markdown("##### KOSPI 환경 — 최근 60일")
        last60 = regime3.iloc[-60:]
        color_map = {"STRONG_BULL": 2, "WEAK": 1, "BEAR": 0}
        ys = last60.map(color_map).values
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=last60.index, y=ys, mode="markers",
            marker=dict(
                size=10,
                color=[REGIME3_INFO[r]["color"] for r in last60.values],
                line=dict(width=0),
            ),
            hovertext=last60.values, hoverinfo="x+text",
        ))
        fig.update_layout(
            height=130, margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(tickvals=[0, 1, 2],
                       ticktext=["🔴 BEAR", "🟡 WEAK", "🟢 BULL"],
                       range=[-0.5, 2.5]),
            xaxis_title="", showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    if today_reg3 == "BEAR":
        st.info(
            "🔴 BEAR 모드: 방어적 단기 반등(Disparity) 신호만 가동. "
            "신호 개수 적은 게 정상. 자본 대부분 현금 유지."
        )

    if sig_df is None or sig_df.empty:
        st.info(f"오늘은 진입 신호가 없습니다 ({info['label']} 환경, {info['strategy']} 룰).")
        st.stop()

    st.markdown(f"##### {len(sig_df)} entry candidate(s) — order at next session's OPEN")

    show = sig_df.copy()
    show["close"] = show["close"].map(lambda x: f"{x:,.0f}")
    # signals_*.csv may use 'score' (new) or 'ret5d' (legacy)
    score_col = "score" if "score" in show.columns else "ret5d"
    show[score_col] = show[score_col].map(lambda x: f"{x*100:.2f}%" if abs(x) < 100 else f"{x:.2f}")
    show["alloc_krw"] = show["alloc_krw"].map(lambda x: f"{x:,.0f}")
    show["stop_price"] = show["stop_price"].map(lambda x: f"{x:,}")
    show["target_price"] = show["target_price"].map(lambda x: f"{x:,}")
    cols_to_show = [c for c in
                    ["code", "name", "board", "close", score_col,
                     "shares", "alloc_krw", "stop_price", "target_price", "max_hold"]
                    if c in show.columns]
    show = show[cols_to_show]
    rename_map = {
        "code": "코드", "name": "종목명", "board": "시장", "close": "종가",
        "score": "신호점수", "ret5d": "5일수익률",
        "shares": "매수주수", "alloc_krw": "배분(KRW)",
        "stop_price": "손절가", "target_price": "목표가", "max_hold": "최대보유일",
    }
    show.columns = [rename_map.get(c, c) for c in show.columns]
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.caption(
        f"진입: 다음 거래일 시가 매수.  손절 {info['stop']} / 목표 {info['target']} / "
        f"최대 보유 {info['hold']}. (현재 환경: **{info['label']}**, 전략: **{info['strategy']}**)"
    )

    csv = sig_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Download signals.csv",
        data=csv,
        file_name=f"signals_{datetime.today().strftime('%Y-%m-%d')}.csv",
        mime="text/csv",
    )

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Today's Signals** 페이지는 **전일 종가 데이터로 산출한 다음 거래일 매수 후보**를 보여줍니다.

**상단 환경 카드 (Triple-Mode):** KOSPI 환경에 따라 자동으로 다른 전략이 활성화됩니다.
- 🟢 **STRONG_BULL** → `Momentum5` (5일 +10% 모멘텀 · 손절 −3% / 익절 +15% / 보유 7일)
- 🟡 **WEAK** → `NewHigh52w` (52주 신고가 돌파 · 손절 −7% / 익절 +20% / 보유 10일) — **메인 알파, 외인 매수 booster 적용**
- 🔴 **BEAR** → `Disparity` (이격도 −10% 단기 반등 · 손절 −3% / 익절 +10% / 보유 3일) — 방어적

**메트릭 카드:**
- **Capital / slot**: 1포지션당 배분되는 금액 (좌측 사이드바 Capital ÷ Max positions)
- **최근 60일 분포**: 🟢 STRONG_BULL / 🟡 WEAK / 🔴 BEAR 일수
- **거래 가능?**: 환경별 진입 강도 (적극 / 제한 / 방어적)

**KOSPI 환경 ribbon:** 최근 60일간 매일의 3-tier 분류. 환경 전환 시점 파악용.

**후보 종목 표 컬럼:**
- `신호점수`: 환경별 다른 정의 (STRONG_BULL=5일 수익률, WEAK=신고가 거리+외인 z-score 가산, BEAR=이격도). 큰 순서로 정렬.
- `매수주수`: 다음날 시가에 실제로 발주할 주식 수
- `손절가` / `목표가`: 매수 직후 HTS에 OCO 예약 등록할 가격 — **환경별로 값이 다름**
- `최대보유일`: 도달 못 하면 그날 종가 시장가 청산 (STRONG_BULL 7일 / WEAK 10일 / BEAR 3일)

**외인 매수 booster (2026-05-22 도입):** WEAK 환경 한정으로 외인 5일 누적 순매수의 60일 z-score를 신호점수에 가산. 동일 점수대 후보 중 외인이 강한 종목 우선 선택. KRX 자격증명(`KRX_ID/KRX_PW`) 없으면 자동 비활성화.

**진입 가정:** 실제 진입은 **다음 거래일 09:00 시가**. 갭 상승(+5% 이상)이면 손절폭이 커지므로 그 종목은 스킵 권장.

자세한 운영 절차는 사이드바의 **Manual** 페이지 참조.
""")


# ============================================================
# Page: Manual (실행 매뉴얼)
# ============================================================
elif page == "Manual":
    st.subheader("📖 EDITH 실행 매뉴얼 (Triple-Mode)")
    st.caption("매일 무엇을 어떻게 해야 하는지. KOSPI 환경(STRONG_BULL / WEAK / BEAR)에 따라 자동으로 다른 전략이 활성화됩니다.")

    with st.expander("📊 0. 백테스트 결과 종합 (2010-2024)", expanded=True):
        st.markdown("""
### Triple-Mode 성과 (실효 11년: 2014-03 ~ 2024-12)

| 항목 | 값 |
|---|---|
| 초기 자본 | 10,000,000 KRW |
| **최종 자본** | **43,858,919 KRW (4.39배)** |
| **CAGR** | **14.63%** |
| **Sharpe** | **0.72** |
| MDD (최대 낙폭) | −27.70% |
| 총 거래수 | 1,366 |
| 평균 보유일 | 4.3일 |
| 승률 | 36.3% |
| 청산 분포 | 손절 57% · 시간청산 27% · 익절 16% |

### 비교 (같은 기간)

| 전략 | CAGR | Sharpe | MDD |
|---|---:|---:|---:|
| **Triple-mode** | **14.6%** | **0.72** | −27.7% |
| Legacy Momentum5 (구버전) | 1.8% | 0.22 | −74.6% |
| KOSPI 단순 보유 | 2.3% | 0.23 | −43.9% |

### 연도별 수익률 (KOSPI 비교)

| 연도 | EDITH | KOSPI | 핵심 사건 |
|---|---:|---:|---|
| 2014 | +32% | −5% | KOSPI 박스권 |
| 2015 | −11% | +2% | 차이나 쇼크 |
| 2016 | −7% | +3% | 박스권 |
| 2017 | +35% | +22% | 강세장 |
| 2018 | **+12%** | **−17%** | **미중 무역분쟁** |
| 2019 | +9% | +8% | 횡보 |
| 2020 | **+69%** | +31% | **코로나 V회복** |
| 2021 | −3% | +4% | 횡보 |
| 2022 | **+6%** | **−25%** | **러시아·금리 약세장** |
| 2023 | +33% | +19% | 회복 |
| 2024 | +6% | −10% | 약세장 |

**핵심 관찰:**
- **약세장에서도 안 잃음** (2018 KOSPI -17% / EDITH +12%, 2022 KOSPI -25% / EDITH +6%, 2024 KOSPI -10% / EDITH +6%)
- 11년 중 손실년 3번뿐 (2015/2016/2021) — 모두 한 자릿수 손실
- 가장 좋은 해 2020 +69%는 코로나 V회복 + 박스권 → 강세장 전환 시기로, **WEAK 환경의 NH52w가 폭발**

### 환경별 알파 기여도 분해

| 환경 | 시간 비중 | 거래수 | 평균손익 | 합산손익 | **알파 기여** |
|---|---:|---:|---:|---:|---:|
| 🟢 STRONG_BULL | 19% | 545 | +0.23% | +123% | 14.5% |
| 🟡 **WEAK** | 28% | 610 | +0.87% | +533% | **62.5%** ⭐ |
| 🔴 BEAR | 52% | 211 | +0.93% | +196% | 23.0% |

**WEAK가 전체 수익의 62.5% 기여 — 진짜 메인 엔진.**
""")

    with st.expander("🎯 1. Triple-Mode란? — 환경별 다른 전략", expanded=False):
        st.markdown("""
EDITH는 매일 KOSPI 환경을 자동 진단해 **3가지 모드 중 하나**를 활성화합니다.
환경마다 **손절/익절/보유일이 다르므로** Today's Signals에 표시된 값을 그대로 따르세요.

| 환경 | 활성 전략 | 진입 조건 | 손절 | 익절 | 보유 |
|---|---|---|---|---|---|
| 🟢 **STRONG_BULL**<br>강한 추세장 | Momentum5 | 5일 +10% 모멘텀 | −3% | +15% | 7거래일 |
| 🟡 **WEAK** ⭐<br>박스권/약추세 | NewHigh52w | 52주 신고가 돌파 | −7% | +20% | 10거래일 |
| 🔴 **BEAR**<br>약세장 | Disparity | 이격도 -10% 단기반등 | −3% | +10% | 3거래일 |

**검증 (2010-2024 15년):** CAGR 14.6% / Sharpe 0.72 / MDD −28%  (vs KOSPI: 2.3% / 0.23 / −44%)

**환경 판별 기준 (KOSPI 일봉):**
- STRONG_BULL: Close > SMA200 AND SMA50 > SMA200 AND SMA50 20일 기울기 > 2% AND 60일 ROC > 3%
- BEAR: Close < SMA200 OR SMA50 < SMA200
- WEAK: 그 외 모든 경우

**가장 중요한 변화:**
- 박스권 9년(2010-2018) 시기 Legacy 전략: CAGR **−17.5%** (자본 -60% 손실)
- Triple-mode: CAGR **+10.8%** (자본 +64%) ✅
- 박스권에서 NewHigh52w가 진짜 알파 원천
""")

    with st.expander("🟢 2-A. STRONG_BULL 전략 상세 (Momentum5)"):
        st.markdown("""
### 진입 조건 (4가지 모두 충족 시)
1. KOSPI 환경 = **STRONG_BULL**
2. 종목의 **5일 수익률 ≥ +10%**
3. 종목의 **종가 > 20일 이동평균** (단기 상승 추세 확인)
4. 종목의 **거래량 > 20일 평균 거래량** (관심 유입 확인)

### 청산 룰
- **손절: −3%** (진입가의 0.97배)
- **익절: +15%** (진입가의 1.15배)
- **시간청산: 7거래일** 후 종가 시장가 매도

### 왜 모멘텀인가
- 강세장은 **모두가 다 오르는 시기** — 종목 선별 alpha 만들기 어려움
- "이미 강하게 오른 종목"이 추가로 더 오를 확률은 통계적으로 유의
- 5일 +10%는 **단기 폭발력**이 검증된 종목만 필터링
- 7일 안에 +15% 더 갈 가능성이 평균보다 높음

### 강점 / 약점
- ✅ 강세장 폭발 시기 잘 따라감 (2017, 2020, 2023)
- ✅ 손절 -3%로 빠른 손실 컷
- ❌ R:R 5:1 (3% : 15%) → 승률 26%로 낮음 — 손절 자주 맞음
- ❌ 강세장 자체가 잘 가니 KOSPI 대비 alpha 작음 (within-regime Sharpe 0.27)

### 이 환경의 알파 기여도: **14.5%** (보너스 시기)

### 운용 팁
- 신호 잘 나오는 시기. 5종목 전부 채워질 수 있음
- 갭상승 +5% 이상이면 그 종목 스킵 (R:R 무너짐)
- 강세장이라 손절 맞아도 빠른 재진입 기회 옴 → 미련 두지 말기
""")

    with st.expander("🟡 2-B. WEAK 전략 상세 (NewHigh52w) ⭐ 메인"):
        st.markdown("""
### 진입 조건 (3가지 모두 충족 시)
1. KOSPI 환경 = **WEAK** (박스권/약한 추세장)
2. 종목의 **종가 > 직전 252거래일(약 1년) 최고가** — 진짜 추세 전환
3. 종목의 **거래량 > 20일 평균 × 1.2배** (관심 유입 확인)

### 청산 룰
- **손절: −7%** (진입가의 0.93배) — 박스권은 변동성 크니 손절 폭 넓힘
- **익절: +20%** (진입가의 1.20배) — 진짜 추세 전환이면 크게 오름
- **시간청산: 10거래일** 후 — 박스권은 추세 형성에 시간 필요

### 왜 박스권에서 52주 신고가인가
- KOSPI 자체가 횡보하는 박스권에는 **시장과 별개로 진짜 강한 종목이 가끔 나옴**
- 1년 최고가를 뚫는다는 건 **숨겨진 펀더멘털 개선 / 산업 변화의 신호**
- 박스권에서는 이런 종목이 **상대적으로 더 큰 폭으로 상승** (시장 평균 대비 outperform)
- 일반 투자자는 박스권에 흥미 잃고 떠나기 때문에 **호재가 더 늦게 반영** → 시장 비효율 활용

### 강점 / 약점
- ✅ **EDITH의 진짜 알파 원천** — 11년 누적 수익의 62.5% 기여
- ✅ 승률 **43.4%** (3개 전략 중 가장 높음)
- ✅ 평균손익 +0.87% (3개 중 압도적)
- ✅ KOSPI가 안 갈 때 +alpha 만듦 (2014, 2016, 2019)
- ❌ 시간청산이 27%로 발생 (10일 안에 +20% 못 가는 경우)
- ❌ 손절 -7%가 깊음 — 한 번 맞으면 큰 손실

### 이 환경의 알파 기여도: **62.5%** ⭐ 진짜 메인 엔진

### 운용 팁
- **WEAK 시기에 자리비움 금지** — 메인 알파 놓침
- 신호가 적은 게 정상 (박스권이라). 하루에 0~2종목이 많음
- 신호 나오면 진지하게 매수 — 큰 알파 가능성
- 손절 -7% 깊으니 **포지션당 자본 비중 정확히 지키기** (4슬롯 또는 5슬롯 균등)
""")

    with st.expander("🔴 2-C. BEAR 전략 상세 (Disparity)"):
        st.markdown("""
### 진입 조건 (2가지 모두 충족 시)
1. KOSPI 환경 = **BEAR** (약세장)
2. 종목의 **이격도 ≤ −10%** (Close / SMA20 − 1 ≤ -0.10)
   = 20일 이동평균보다 10% 이상 아래로 빠진 종목
3. 종목의 **종가 > 60일 이동평균** — 장기 추세는 살아있는 종목만 (낙하칼 회피)

### 청산 룰
- **손절: −3%** (진입가의 0.97배) — 약세장이라 짧게 손절
- **익절: +10%** (진입가의 1.10배) — 약세장에 단기 반등 목표는 작게
- **시간청산: 3거래일** 후 — 가장 짧음. 약세장은 길게 들고 있으면 위험

### 왜 약세장에 이격도 반등인가
- 약세장에 종목들이 **공포 매도로 펀더멘털 대비 과도 하락**하는 경우 많음
- 20일 평균 -10%는 통계적으로 **단기 반등 가능성이 평균보다 높은** 영역
- 단, 60일 평균 위라는 조건 → "장기 추세는 아직 안 죽은 종목" 한정 (망하는 종목 회피)
- 3일 안에 +10% 반등하면 익절, 아니면 빠르게 시간청산

### 강점 / 약점
- ✅ **약세장에서도 +alpha** 가능 (2018 KOSPI -17% / EDITH +12%, 2022 -25% / +6%)
- ✅ MDD 한정적 (within-regime -8.8%) — 큰 손실 위험 작음
- ✅ 평균손익 +0.93% (의외로 가장 높음 — 깊은 하락 후 반등 강함)
- ❌ 거래 빈도 매우 낮음 (전체 거래의 15%만)
- ❌ 잘못하면 진짜 폭락에 휘말림 (60일 평균 필터로 일부 차단)

### 이 환경의 알파 기여도: **23.0%** (방어 + 가끔 한 입)

### 운용 팁
- 시간의 52% (절반 이상)가 BEAR — 그래서 거래 적은 게 정상
- 거래 없는 날이 며칠씩 계속됨 — 정상
- 신호 나오면 빠르게 진입 (3일 안에 끝나니 망설이지 말 것)
- 약세장이라 보유 종목 1~2개 정도가 적정. 5종목 다 채우려 무리하지 말 것
""")

    with st.expander("🕐 3. 주간 시간표 (월~금)", expanded=False):
        st.markdown("""
| 시간 (KST) | 할 일 | 어디서 |
|---|---|---|
| **06:00 ~ 07:00** | (선택) JARVIS 결과 확인 | JARVIS 윈도우 또는 그냥 안 해도 OK |
| **08:55 ~ 09:00** | 어제 등록한 예약 매수 자동 체결 확인 | HTS |
| **09:00 직후** | 매수 체결되면 **즉시 OCO 손절/익절 등록** | HTS |
| **09:00 ~ 15:30** | 아무것도 하지 않음 (장중 개입 금지) | — |
| **15:30** | 한국 장 마감 | — |
| **15:35** | `edith` 실행 → 시그널 자동 산출 + GitHub push | EDITH 윈도우 터미널 |
| **15:35 ~ 15:40** | Streamlit Cloud 자동 재배포 대기 | — |
| **15:40 ~ 16:30** | 휴대폰/대시보드에서 내일 매수 후보 확인 + HTS 예약 등록 | EDITH 앱 |
| **22:30 (서머타임) / 23:30** | 미국 장 개장 (JARVIS는 분기 리밸런싱이라 액션 없음) | — |

**금요일 특이사항:**
- 금요일 16:00에 EDITH가 시그널을 산출하면 → 다음 거래일 = **월요일 09:00** 매수
- 주말 동안 큰 뉴스가 있으면 월요일 시가가 갭상승/하락할 수 있음
- 시가가 종가 대비 **+5% 이상 갭상승**이면 그 종목은 **스킵** 권장 (손절폭 커짐)
- 시가가 -3% 이상 갭하락이면 손절선 즉시 터치하므로 매수 자체가 무의미 — 그 종목 스킵
""")

    with st.expander("📥 2. 매수 절차 (시그널 개수별 대응)"):
        st.markdown("""
EDITH 자본을 **균등 분할**해서 **신호 종목 모두에 매수**합니다.

기본값: Capital = **10,000,000 KRW** / Max positions = **5**

| 오늘 신호 종목 수 | 매수 종목 수 | 1종목당 금액 | 남는 현금 |
|---|---|---|---|
| 0개 (또는 KOSPI 레짐 🔴) | **매수 안 함** | — | 1,000만원 전액 대기 |
| 1개 | 1종목 | 200만원 | 800만원 대기 |
| 2개 | 2종목 | 200만원씩 | 600만원 대기 |
| 3개 | **3종목 모두** | 200만원씩 | 400만원 대기 |
| 4개 | 4종목 | 200만원씩 | 200만원 대기 |
| 5개 이상 | 상위 5종목 (5일수익률 큰 순) | 200만원씩 | 0원 |

**왜 균등? 왜 남은 현금은 그대로 두는가?**
- 신호 안 나온 날엔 시장에 우위가 없음 → 무리하게 비중 올리지 않음
- 1슬롯당 200만원 고정이라 5종목 다 채워야 100% 가동. 부분 가동도 정상.
- 보유 종목이 청산되면 빈 슬롯이 생기고, 다음 신호 발생 시 재투입.

**현재 보유 중인 종목이 있으면?**
- 보유 중인 종목은 슬롯 점유. 빈 슬롯에만 새 매수.
- 예: 어제 2종목 매수했고 청산 안 됐는데 오늘 새 신호 3종목 발생 → 빈 슬롯 3개에 신호 3종목 매수.

**매수 방식 선택:**
- **권장**: 09:00 시가 시장가 주문 (백테스트와 일치)
- **대안**: 09:00 동시호가 지정가 (전일 종가 +1~2 호가) — 미체결 시 09:10까지 기다리고 시장가 전환
- **갭 회피 룰**: 시가가 전일 종가 대비 **+5% 이상 갭업**이면 그 종목은 매수 취소
""")

    with st.expander("🛡️ 3. 손절가 / 익절가 자동 계산 (환경별)"):
        st.markdown("""
**환경에 따라 다른 룰** — Today's Signals 페이지에서 오늘의 환경을 먼저 확인하세요.

| 환경 | 손절 | 익절 | 보유 |
|---|---|---|---|
| 🟢 STRONG_BULL | −3% | +15% | 7거래일 |
| 🟡 WEAK | −7% | +20% | 10거래일 |
| 🔴 BEAR | −3% | +10% | 3거래일 |

매수 직후 **반드시 OCO 매도 주문 등록**. 매도 두 개(손절+익절) 중 하나 체결되면 다른 하나 자동 취소.

---

**📱 가격 계산기 (환경 + 진입가 + 주수 입력):**
""")
        col0, col1, col2 = st.columns(3)
        with col0:
            calc_regime = st.selectbox(
                "환경",
                options=["STRONG_BULL", "WEAK", "BEAR"],
                index=0,
                help="Today's Signals 페이지에서 본 오늘의 환경",
            )
        with col1:
            entry_price = st.number_input(
                "진입가 (KRW)",
                min_value=1, max_value=10_000_000, value=100_000, step=100,
            )
        with col2:
            num_shares = st.number_input(
                "매수 주수",
                min_value=1, max_value=100_000, value=20, step=1,
            )

        regime_params = {
            "STRONG_BULL": (0.03, 0.15, 7),
            "WEAK":        (0.07, 0.20, 10),
            "BEAR":        (0.03, 0.10, 3),
        }
        stop_p, target_p, hold_d = regime_params[calc_regime]
        stop_price = int(round(entry_price * (1 - stop_p)))
        target_price = int(round(entry_price * (1 + target_p)))
        invest = entry_price * num_shares
        max_loss = (stop_price - entry_price) * num_shares
        max_gain = (target_price - entry_price) * num_shares

        c1, c2, c3 = st.columns(3)
        c1.metric("💰 투자 금액", f"{invest:,} KRW")
        c2.metric(f"🔻 손절가 (-{stop_p*100:.0f}%)", f"{stop_price:,}", f"{max_loss:,} KRW")
        c3.metric(f"🎯 익절가 (+{target_p*100:.0f}%)", f"{target_price:,}", f"+{max_gain:,} KRW")
        st.caption(
            f"위 손절가 / 익절가를 HTS의 OCO 매도 주문에 그대로 입력. "
            f"보유 기간 안에 둘 다 안 맞으면 **{hold_d}거래일째 종가에 시장가 청산** (시간 청산). "
            "실제 P&L은 약 0.42% 비용 차감 후."
        )

    with st.expander("📲 4. 주문 종류 정리 — 예약매수 vs OCO매도"):
        st.markdown("""
EDITH 운영에는 **두 가지 다른 주문**이 쓰입니다. 헷갈리지 마세요.

| 구분 | **① 예약 매수** | **② OCO 매도** |
|---|---|---|
| **시점** | 금요일/평일 16시쯤 (전일 저녁) | 매수 체결 직후 (당일 09:01) |
| **방향** | 매수 (사는 주문) | **매도** (파는 주문) |
| **주문 수** | 1개 | **2개 묶음** (손절+익절) |
| **목적** | 다음날 시가에 자동 매수 | 장중 안 봐도 자동 청산 |

---

### ① 예약 매수 (Reserve / Day 주문)

전일 저녁에 "내일 09:00에 시장가로 사라"고 미리 등록해두는 주문.

**HTS 메뉴 위치:**
- 키움증권 영웅문: [주식주문 → 예약주문]
- 한국투자증권 eFriend: [매매 → 예약주문]
- 미래에셋 KAIROS: [주식 → 예약주문]

**입력값**:
- 종목: EDITH 신호 종목 코드
- 구분: **매수**
- 수량: 대시보드의 `매수주수`
- 가격: **시장가** (또는 동시호가 지정가 +1~2호가)
- 예약일: 다음 거래일
- 시간: **장 시작 (09:00)** 또는 동시호가 (08:30~)

신호 종목이 3개면 위 예약을 **3건 등록**.

---

### ② OCO 매도 (One-Cancels-Other)

이미 매수해서 보유 중인 종목에 대해, **매도 주문 두 개를 동시에** 걸어두고
어느 한 쪽이 먼저 체결되면 다른 한 쪽이 자동 취소되는 주문 묶음.

**HTS 메뉴 위치:**
- 키움 영웅문: [주식주문 → OCO주문] 또는 [스탑로스]
- 한국투자증권 eFriend: [매매 → OCO 주문]
- 미래에셋 KAIROS: [주식 → 자동주문 → OCO]

**입력값** (예: 삼성전자 100주를 80,000원에 매수한 경우):
| 매도 1 (익절) | 매도 2 (손절) |
|---|---|
| 가격: 92,000원 (+15%) | 트리거: 77,600원 (-3%) |
| 방식: **지정가 매도** | 방식: **스탑 매도** (도달 시 시장가) |
| 수량: 100주 | 수량: 100주 |
| 유효기간: 1개월 (또는 무기한) | 유효기간: 1개월 |

신호 종목이 3개면 위 OCO를 **3건 등록** (각 종목마다 따로).

---

### 5거래일 시간청산은 어떻게?

OCO는 손절/익절만 처리하므로, **5거래일 보유 후 강제 청산**은 별도로:
- **방법 A**: 6거래일째 아침에 수동으로 시장가 매도 (가장 확실)
- **방법 B**: HTS의 "지정일 예약매도" 기능으로 5거래일 후 종가 매도 예약
- **방법 C**: 핸드폰 알림으로 5일 카운트다운만 띄우고 수동 처리

권장: **방법 A** (가장 단순 + 실수 적음). 매수일에 캘린더 메모로 청산일 표시.
""")

    with st.expander("🚨 5. 약세장 (KOSPI 레짐 🔴) 대처"):
        st.markdown("""
대시보드 상단이 🔴 BEARISH로 바뀌면:

1. **신규 매수 즉시 중단**. 어제 등록해둔 다음날 예약 매수도 **취소**.
2. **이미 보유 중인 포지션은 그대로 관리** — 손절/익절/시간청산 룰 적용. 임의 청산 금지.
3. 보유 종목 모두 청산 끝나면 EDITH는 **휴면 상태**. 자본은 그대로 대기.
4. 레짐이 🟢으로 다시 바뀌면 다음 시그널부터 자동 재가동.

**역사적 사례:**
- 2022년 KOSPI -25% 시기 → EDITH 거래 0건 (자동 회피)
- 그래서 백테스트 MDD가 -27%로 막혔음 (필터 없으면 -79%)

레짐 OFF 기간이 길어지면 (1개월 이상) 다른 안전자산(예: 적금, 단기 채권 ETF)으로 옮겨두는 것도 고려.
""")

    with st.expander("❓ 6. 자주 발생하는 케이스"):
        st.markdown("""
**Q1. 어제 신호가 3개 나왔는데 오늘 또 3개 나왔어요. 보유 종목은 어떻게?**
- 보유 종목은 그대로. 빈 슬롯이 없으면 새 신호는 패스.
- 예: 어제 3종목 매수(보유) + 오늘 3종목 신호 → 빈 슬롯 2개 → 오늘 신호 중 상위 2개만 매수.

**Q2. 매수 주문이 시가에 체결 안 됐어요.**
- 그날 그 종목은 패스. 09:10 이후 추격 매수 금지.
- 다음 날 같은 신호가 또 나오면 그때 시도.

**Q3. 시가가 전일 종가 대비 +5% 갭상승**
- 매수 취소. 손절선이 -8%까지 벌어져서 R:R이 무너짐.

**Q4. 보유 중인 종목이 상한가 (+30%) 도달**
- 익절가(+15%)에서 이미 체결됐을 것. 추가로 갖고 있으려 하지 말 것.
- 다음날 갭다운 가능성 매우 높음.

**Q5. 보유 중 거래정지 / 단일가 매매로 전환**
- 거래 재개 후 시장가 즉시 청산. 익절/손절 무관.

**Q6. 5거래일이 지났는데 손절도 익절도 안 맞고 횡보 중**
- **5일째 종가에 무조건 시장가 매도**. 미련 두지 말 것. (= 시간 청산)
- 다음 신호 자리 비워줘야 함.

**Q7. 한국 공휴일이 끼면 5거래일 카운트는?**
- 거래일 기준으로만 카운트. 예: 수요일 매수 + 목요일 공휴일 → 금요일이 2거래일째, 다음 주 수요일이 5거래일째.

**Q8. 휴가 / 출장으로 며칠 못 봤어요**
- GitHub Actions가 매일 자동으로 시그널 commit해두므로 results/ 폴더에 기록은 있음
- 복귀 첫날: HTS에서 OCO가 자동 발동했는지 확인. 시간 청산 안 된 종목은 즉시 시장가 매도 (이미 5일 초과면 룰 위반이라 청산).
- 가능하면 휴가 전 모든 포지션 청산하고 떠나는 것이 안전.

**Q9. 시그널 종목이 너무 많은 날 (예: 10개)**
- 상위 5개만 (5일수익률 큰 순). 나머지는 무시.
- 신호 강도가 높은 날일수록 강세장 신호. 그래도 max 5포지션 룰 지킬 것.

**Q10. 매수 금액이 1주 가격보다 작으면? (예: 1슬롯 200만원인데 주가 250만원)**
- 그 종목 스킵. 다음 신호 종목으로 대체.
- (드물지만 LG에너지솔루션, 삼성바이오로직스 같은 고가주에서 발생 가능)
""")

    with st.expander("📊 7. 누적 손실 단계별 대응"):
        st.markdown("""
EDITH 자본이 직전 고점 대비 얼마나 줄었는지(drawdown) 기준:

| 손실 단계 | 조치 |
|---|---|
| **0 ~ -10%** | 정상. 그대로 운영 |
| **-10% ~ -20%** | 일시 중단 후 최근 30거래일 로그 분석. KOSPI도 동반 하락이면 정상 |
| **-20% ~ -30%** | 신규 매수 중단. KOSPI 레짐과 무관하게 1주일 관망 |
| **-30% 이상** | **즉시 전체 청산** + 전략 재검토. 백테스트 MDD(-27.5%) 초과 = 모델 깨졌을 가능성 |

확인 방법:
- 대시보드 **Performance 페이지**의 Drawdown 차트에서 현재 위치 비교
- 또는 매월 1일 본인 계좌 잔고 vs 직전 최대치 직접 비교
""")

    with st.expander("📅 8. 분기 정산 / 리튜닝"):
        st.markdown("""
**분기 1회 (3, 6, 9, 12월 첫 주):**

1. **현금 일부 출금** — 누적 수익의 30~50% 정도를 EDITH 외부로 빼서 안전자산 이동
2. **리튜닝 실행** — 로컬 터미널에서:
   ```bash
   edith-tune
   ```
   → 15~20분 후 `results/tuning_robust_top.csv` 갱신
3. **검증 포인트**:
   - 현재 파라미터 (stop=3%, target=15%, ret_thresh=10%) 가 여전히 상위에 있는지
   - (stop=3%, target=15%) 패밀리에서 변종들이 클러스터링하는지
   - 안 그러면 새 파라미터로 교체 필요 → `edith/final_strategy.py` 수정

**연 1회**: 전체 데이터로 다시 백테스트
```bash
edith-test
```
""")

    st.divider()
    st.info(
        "🔖 이 페이지를 휴대폰 즐겨찾기에 추가해두세요. "
        "장 마감 후 시그널 보면서 옆 탭에 펼쳐두면 매일 운영에 편합니다."
    )


# ============================================================
# Page: Performance
# ============================================================
elif page == "Performance":
    st.subheader("📈 Equity Curve")
    equities = load_final_equity()
    if not equities:
        st.warning("No backtest equity curves found. Run `scripts/run_final.py` first.")
        st.stop()

    options = list(equities.keys())
    default = [o for o in ["Triple+Booster (LIVE)", "Triple_full (no booster)", "KOSPI B&H"] if o in options]
    selected = st.multiselect("Curves", options=options, default=default)

    fig = go.Figure()
    for name in selected:
        eq = equities[name]
        normalised = eq / eq.iloc[0]
        fig.add_trace(go.Scatter(
            x=normalised.index, y=normalised.values,
            mode="lines", name=name,
        ))
    fig.update_layout(
        height=480, hovermode="x unified",
        yaxis_title="Equity (x initial)", xaxis_title="",
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("##### Drawdown")
    fig_dd = go.Figure()
    for name in selected:
        eq = equities[name]
        dd = eq / eq.cummax() - 1.0
        fig_dd.add_trace(go.Scatter(
            x=dd.index, y=dd.values * 100,
            mode="lines", name=name, fill="tozeroy",
        ))
    fig_dd.update_layout(
        height=260, hovermode="x unified",
        yaxis_title="Drawdown (%)", xaxis_title="",
        legend=dict(orientation="h", y=-0.25),
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    st.markdown("##### Summary table")
    summary = load_final_summary()
    if not summary.empty:
        fmt = {}
        for c in summary.columns:
            if c in ("CAGR", "MDD", "WinRate", "AvgWin", "AvgLoss"):
                fmt[c] = "{:.2%}"
            elif c in ("Sharpe", "Sortino", "Calmar", "Final", "ProfitFactor", "AvgHoldDays"):
                fmt[c] = "{:.3f}"
            elif c == "N_trades":
                fmt[c] = "{:.0f}"
        st.dataframe(summary.style.format(fmt), use_container_width=True)

    st.markdown("##### Yearly returns")
    yearly = compute_yearly_returns({k: v for k, v in equities.items() if k in selected})
    if not yearly.empty:
        styled = yearly.style.format("{:.2%}", na_rep="—").background_gradient(cmap="RdYlGn", axis=None)
        st.dataframe(styled, use_container_width=True)

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Performance** 페이지는 **2014-03 ~ 2024-12 (실효 11년 walk-forward)** 백테스트 결과를 보여줍니다.

**Equity Curve (위 차트):**
- Y축 = 초기 자본 대비 배수. 1.0에서 시작.
- 🟢 **Triple+Booster (LIVE)** — 현재 운용 전략. 3-tier 디스패처 + WEAK regime 외인 매수 booster
- ⚪ **Triple_full (no booster)** — booster 빠진 베이스라인 (booster 기여도 시각화용)
- ⚪ **KOSPI B&H** — KOSPI 지수 매수 후 보유. alpha 비교용
- 🔴 **Legacy: …** — 옛 단일 전략 결과 (2020-2026 강세장 표본만, 박스권 검증 없음 → **실제 운용 기준 아님**)

**Drawdown 차트:** 각 시점에서 직전 고점 대비 얼마나 빠졌는지(%). 0이 고점 갱신 중, 음수가 깊을수록 아픈 시기. **MDD(최대 손실폭)** = 차트의 가장 깊은 골짜기.

**Summary table 주요 컬럼:**
- `CAGR`: 연복리 수익률
- `Sharpe`: 위험 대비 수익 (1 이상이면 우수, 2 이상은 매우 우수)
- `Sortino`: 하방 변동성만 고려한 Sharpe
- `MDD`: 백테스트 기간 최대 손실폭 (음수)
- `ProfitFactor`: 총 이익 ÷ 총 손실 (>1이면 흑자)

**Yearly returns:** 각 전략의 연도별 수익률 히트맵. 초록=수익 / 빨강=손실. **약세장(2022, 2024)에서도 손실이 작거나 양수인 것이 Triple-Mode의 핵심 — BEAR 환경에서 Disparity로 제한 가동, WEAK에서 외인 booster로 진짜 알파 종목만 선별.**
""")


# ============================================================
# Page: Trades
# ============================================================
elif page == "Trades":
    st.subheader("📜 Trade Log")
    trades_map = load_final_trades()
    if not trades_map:
        st.warning("No trade logs found.")
        st.stop()

    options = list(trades_map.keys())
    default_idx = options.index("Triple+Booster (LIVE)") if "Triple+Booster (LIVE)" in options else 0
    strat = st.selectbox("Strategy", options=options, index=default_idx)
    df = trades_map[strat]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", f"{len(df):,}")
    c2.metric("Win rate", f"{(df['pnl_pct'] > 0).mean()*100:.1f}%")
    c3.metric("Avg P&L", f"{df['pnl_pct'].mean()*100:.2f}%")
    c4.metric("Total P&L (sum)", f"{df['pnl_pct'].sum()*100:.0f}%")

    reason_counts = df["reason"].value_counts()
    fig_r = px.pie(values=reason_counts.values, names=reason_counts.index,
                   title="Exit reasons", hole=0.4)
    fig_r.update_layout(height=300, margin=dict(l=10, r=10, t=40, b=10))

    fig_d = px.histogram(df, x="pnl_pct", nbins=40,
                         title="P&L distribution (per trade)")
    fig_d.update_layout(height=300, margin=dict(l=10, r=10, t=40, b=10),
                        xaxis_tickformat=".0%")

    cA, cB = st.columns(2)
    cA.plotly_chart(fig_r, use_container_width=True)
    cB.plotly_chart(fig_d, use_container_width=True)

    st.markdown("##### Recent trades")
    show = df.sort_values("exit_date", ascending=False).head(200).copy()
    show["pnl_pct"] = show["pnl_pct"].map(lambda x: f"{x*100:.2f}%")
    show["entry_price"] = show["entry_price"].map(lambda x: f"{x:,.0f}")
    show["exit_price"] = show["exit_price"].map(lambda x: f"{x:,.0f}")
    show["pnl_krw"] = show["pnl_krw"].map(lambda x: f"{x:,.0f}")
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Trades** 페이지는 **백테스트에서 발생한 모든 매매 1건 1건**을 보여줍니다. 실제 거래가 아니라 **시뮬레이션 기록**입니다.

**전략 선택:** `Triple+Booster (LIVE)`가 현재 운용 전략. Legacy 옵션은 옛 단일 전략 기록(2020-2026 표본).

**상단 4개 카드:**
- `Trades`: 백테스트 전체 기간 거래 수 (Triple+Booster는 11년간 약 1,340건)
- `Win rate`: 익절·시간청산 중 수익으로 끝난 비율 (Triple+Booster ~37%)
- `Avg P&L`: 거래 1건당 평균 손익률. 0보다 크면 흑자 시스템
- `Total P&L`: 모든 거래 손익률 단순 합산 (참고용)

**Exit reasons 도넛:** 환경별 손절·익절 폭이 다르므로 분포도 환경 비중에 따라 달라짐.
- `stop`: 손절 (STRONG_BULL −3% / WEAK −7% / BEAR −3%)
- `target`: 익절 (STRONG_BULL +15% / WEAK +20% / BEAR +10%)
- `time`: 시간 청산 (STRONG_BULL 7일 / WEAK 10일 / BEAR 3일)

> 핵심: **손절은 많고 작게, 익절은 적고 크게**. 비대칭 R:R로 흑자 유지. WEAK regime에서 외인 booster가 동일 후보 중 진짜 알파를 골라내 평균 손익률을 끌어올림.

**P&L distribution 히스토그램:** 거래 1건당 손익률 분포. 손절 부근(−3 ~ −7%)에 큰 막대, 익절 부근(+10 ~ +20%)에 작은 막대, 가운데에 시간청산 분포.

**Recent trades 표:**
- `entry_date` / `exit_date`: 매수일 / 매도일
- `reason`: stop / target / time 중 하나
- `pnl_pct`: 수수료·세금·슬리피지 모두 차감 후 순손익률
""")


# ============================================================
# Page: Strategy Stats
# ============================================================
elif page == "Strategy Stats":
    st.subheader("🎯 Strategy Stats — Triple+Booster (LIVE)")
    trades_map = load_final_trades()
    df = trades_map.get("Triple+Booster (LIVE)")
    if df is None or df.empty:
        st.warning("Live trades CSV not found. Run `scripts/run_foreign_booster_validation.py`.")
        st.stop()

    # ---- Per-regime walk-forward summary (foreign_booster_summary.csv) ----
    p_b = RESULTS_DIR / "foreign_booster_summary.csv"
    if p_b.exists():
        st.markdown("##### Walk-forward by period (B_BoostWEAK_w0.5)")
        bs = pd.read_csv(p_b)
        bs = bs[bs["period"].str.startswith("B_BoostWEAK_w0.5")].copy()
        bs["window"] = bs["period"].str.replace("B_BoostWEAK_w0.5", "").str.strip()
        bs_show = bs[["window", "CAGR", "Sharpe", "MDD", "WinRate", "ProfitFactor", "N_trades"]].set_index("window")
        st.dataframe(
            bs_show.style.format({
                "CAGR": "{:.2%}", "Sharpe": "{:.2f}", "MDD": "{:.2%}",
                "WinRate": "{:.1%}", "ProfitFactor": "{:.2f}", "N_trades": "{:.0f}",
            }),
            use_container_width=True,
        )
        st.caption("IS 2010-2018 (박스권) / OOS_A 2019-2023 / OOS_B 2024 약세장. "
                   "OOS_B Sharpe 0.86이 외인 booster의 핵심 기여.")

    st.divider()

    df["month"] = df["exit_date"].dt.to_period("M").astype(str)
    monthly = df.groupby("month")["pnl_pct"].sum()
    fig_m = go.Figure()
    colors = ["#22c55e" if v > 0 else "#ef4444" for v in monthly.values]
    fig_m.add_trace(go.Bar(x=monthly.index, y=monthly.values * 100, marker_color=colors))
    fig_m.update_layout(height=340, title="Monthly P&L (sum across trades, %)",
                        yaxis_title="%", xaxis_tickangle=-45)
    st.plotly_chart(fig_m, use_container_width=True)

    df["year"] = df["exit_date"].dt.year
    yearly_n = df.groupby("year").size()
    yearly_wr = df.groupby("year").apply(lambda x: (x["pnl_pct"] > 0).mean())
    yearly_pnl = df.groupby("year")["pnl_pct"].sum()

    yt = pd.DataFrame({
        "Trades": yearly_n,
        "WinRate": yearly_wr,
        "Sum P&L": yearly_pnl,
    })
    fmt = {"WinRate": "{:.1%}", "Sum P&L": "{:.2%}", "Trades": "{:.0f}"}
    st.markdown("##### Yearly trade stats")
    st.dataframe(yt.style.format(fmt), use_container_width=True)

    # Hold day distribution
    fig_h = px.histogram(df, x="hold_days", nbins=12, title="Hold days distribution")
    fig_h.update_layout(height=260, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_h, use_container_width=True)

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Strategy Stats** 페이지는 **Triple+Booster (현재 운용 전략) 한정**으로 패턴 분석을 보여줍니다.

**Walk-forward 표:** 14년 기간을 3구간으로 나눠 본 성과.
- **IS 2010-2018**: 박스권 기간 (in-sample, 파라미터 튜닝 구간)
- **OOS_A 2019-2023**: 첫 out-of-sample. Sharpe ~1.14
- **OOS_B 2024**: 가장 약했던 해 (KOSPI −10%). Sharpe 0.86 — 외인 booster 기여 최대

**Monthly P&L 막대그래프:**
- 각 월의 모든 거래 손익률을 단순 합산. 초록=수익월, 빨강=손실월.
- 한 종목당 200만원 × 평균 8건/월 매매 가정하면, 막대 1% ≈ 약 1.5만원 실손익 규모.
- 연속 빨강 막대가 3개월 이상이면 **전략이 깨졌는지 점검 시그널**.

**Yearly trade stats 표:**
- `Trades`: 그 해의 매매 횟수. 환경 비중에 따라 변동 (BEAR 비중 큰 해는 적음).
- `WinRate`: 그 해 승률. 30~40% 사이가 정상 (Triple+Booster 평균 37%).
- `Sum P&L`: 그 해 모든 거래 손익률 합. 50% 이상이면 강세장 + 좋은 시그널 매칭.

**Hold days distribution:**
- 매매 1건이 며칠 만에 끝났는지 분포. 손절 빨리 맞은 1-3일 거래가 다수.
- 환경별 최대 보유일이 달라 분포에 3·7·10일 부근 peak가 보일 수 있음.
""")


# ============================================================
# Page: Config
# ============================================================
elif page == "Config":
    st.subheader("⚙️ Strategy Configuration — Triple-Mode + Foreign Booster")
    st.caption("KOSPI 환경별로 다른 sub-strategy + 파라미터가 자동 활성화됩니다.")

    st.markdown("##### Per-regime sub-strategies")
    regime_rows = [
        {
            "환경": "🟢 STRONG_BULL",
            "전략": "Momentum5",
            "진입 조건": f"5일 수익률 ≥ {PARAMS_STRONG_BULL['ret_thresh']*100:.0f}%, Close > SMA20, Vol > MA20",
            "손절": f"−{PARAMS_STRONG_BULL['stop_pct']*100:.0f}%",
            "익절": f"+{PARAMS_STRONG_BULL['target_pct']*100:.0f}%",
            "최대보유": f"{PARAMS_STRONG_BULL['max_hold']}d",
        },
        {
            "환경": "🟡 WEAK",
            "전략": "NewHigh52w ⭐",
            "진입 조건": f"{PARAMS_WEAK['lookback']}일 신고가 돌파 (vol_mult={PARAMS_WEAK['vol_mult']})",
            "손절": f"−{PARAMS_WEAK['stop_pct']*100:.0f}%",
            "익절": f"+{PARAMS_WEAK['target_pct']*100:.0f}%",
            "최대보유": f"{PARAMS_WEAK['max_hold']}d",
        },
        {
            "환경": "🔴 BEAR",
            "전략": "Disparity",
            "진입 조건": f"이격도 ≤ {PARAMS_BEAR['thresh']*100:.0f}% (SMA20 대비)",
            "손절": f"−{PARAMS_BEAR['stop_pct']*100:.0f}%",
            "익절": f"+{PARAMS_BEAR['target_pct']*100:.0f}%",
            "최대보유": f"{PARAMS_BEAR['max_hold']}d",
        },
    ]
    st.dataframe(pd.DataFrame(regime_rows), use_container_width=True, hide_index=True)

    st.markdown("##### Regime classification (KOSPI index)")
    st.markdown("""
- **STRONG_BULL**: Close > SMA200 AND SMA50 > SMA200 AND SMA50 slope > 2% (20d) AND ROC60 > 3%
- **BEAR**: Close < SMA200 OR SMA50 < SMA200
- **WEAK**: 그 외 (sideways or weak trend)
""")

    st.markdown("##### Foreign net-buy score booster (WEAK only ⭐)")
    st.markdown("""
- 입력: KRX 일별 외국인 순매수 (KRW), pykrx
- 변환: 5일 누적의 60일 z-score를 clip(−2, +2) × weight=0.5
- 적용: WEAK regime 진입 신호의 score에 ADD (entry는 차단 안 함, 순위만 재조정)
- 효과: max_positions=5에서 동시 후보 중 외인이 강한 종목 우선 선택
- 의존성: `KRX_ID` / `KRX_PW` 환경변수. 미설정 시 자동 비활성화 (baseline fallback).
""")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Universe**")
        st.markdown(f"""
- KOSPI top **{N_KOSPI}** by market cap
- KOSDAQ top **{N_KOSDAQ}** by market cap
- ETF / SPAC / preferred excluded
""")
        st.markdown("**Sizing**")
        st.markdown(f"""
- Capital: configurable via sidebar
- Max positions: configurable (default 5)
- Allocation: equal weight per slot
""")
    with c2:
        st.markdown("**Cost model (2025+ KR market)**")
        st.markdown("""
- Buy fee: 0.015%
- Sell fee: 0.015% + 거래세 0.18%
- Slippage: 0.10% per side
- Round-trip ≈ 0.42%
""")
        st.markdown("**Backtest sample**")
        st.markdown("""
- 2014-03 ~ 2024-12 (실효 11년)
- 150종목 (KOSPI top100 + KOSDAQ top50)
- Walk-forward: IS 2010-2018 / OOS_A 2019-2023 / OOS_B 2024
""")

    st.divider()
    st.markdown("##### Files")
    st.code(f"""
Daily signal:   ./venv/bin/python scripts/daily_signal.py --capital {capital:,.0f}
Triple-mode:    ./venv/bin/python scripts/run_triple_mode_validation.py
Booster:        ./venv/bin/python scripts/run_foreign_booster_validation.py
Tune:           ./venv/bin/python scripts/run_tuning.py
Dashboard:      ./venv/bin/streamlit run dashboard.py
    """)

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Config** 페이지는 **현재 운용 중인 Triple-Mode 전략의 모든 설정값**을 한눈에 보여줍니다.

**Per-regime sub-strategies 표:** 환경별 전략·진입조건·손절/익절/보유일. 매일 KOSPI 분류 결과에 따라 자동으로 한 줄이 활성화됨.

**Regime classification:** KOSPI 일봉으로 3-tier 분류하는 규칙. 15년 평균 STRONG_BULL ~19% / WEAK ~28% / BEAR ~52%.

**Foreign booster:** WEAK 한정 외인 매수 가산점. STRONG_BULL에 적용하면 오히려 성과 악화(검증 완료) → WEAK ONLY 고정.

**Cost model:** 백테스트에 반영된 거래 비용. 실제 사용자 계좌 수수료가 더 우대면 백테스트는 보수적 → 실제 수익이 더 클 가능성.

**Files 박스:** 터미널에서 실행할 명령어. 빠른 단축어는 `edith` / `edith-ui` (`~/.zshrc` 참조).
""")
