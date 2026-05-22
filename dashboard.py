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

from edith.final_strategy import final_strategy, FINAL_PARAMS_MOMENTUM
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
    for name in ["Momentum5_tuned", "NewHigh52w_tuned", "Ensemble_M+NH"]:
        p = RESULTS_DIR / f"final_equity_{name}.csv"
        if p.exists():
            s = pd.read_csv(p, index_col=0, parse_dates=True)["equity"]
            out[name] = s
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
    for name in ["Momentum5_tuned", "NewHigh52w_tuned", "Ensemble_M+NH"]:
        p = RESULTS_DIR / f"final_trades_{name}.csv"
        if p.exists():
            df = pd.read_csv(p, parse_dates=["entry_date", "exit_date"])
            out[name] = df
    return out


@st.cache_data(ttl=60 * 15)
def load_final_summary() -> pd.DataFrame:
    p = RESULTS_DIR / "final_summary.csv"
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, index_col=0)


def _load_latest_cached_signals() -> tuple[pd.DataFrame, str | None]:
    """Read the most recently committed signals_YYYY-MM-DD.csv from results/.
    Returns (df, filename) or (empty_df, None)."""
    files = sorted(glob.glob(str(RESULTS_DIR / "signals_*.csv")), reverse=True)
    if not files:
        return pd.DataFrame(), None
    latest = files[0]
    df = pd.read_csv(latest, dtype={"code": str})
    # Re-derive display columns the script saved
    return df, Path(latest).name


def _load_cached_regime_series() -> pd.Series:
    """Build a regime series from cached KOSPI index pkl or a committed CSV."""
    p_reg = RESULTS_DIR / "regime_series.csv"
    if p_reg.exists():
        s = pd.read_csv(p_reg, index_col=0, parse_dates=True)["regime"].astype(bool)
        return s
    return pd.Series(dtype=bool)


@st.cache_data(ttl=60 * 5)
def compute_today_signals(capital: float, top_n: int, force: bool, allow_live: bool):
    """Returns (today_regime: bool|None, regime_series: pd.Series, signals_df: pd.DataFrame, source: str).

    `source` is 'live' (recomputed now) or 'cache' (read from committed CSV).
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
                    if candidates:
                        df_live = pd.DataFrame(candidates).sort_values("ret5d", ascending=False).head(top_n)
                        per_slot = capital / top_n
                        df_live["alloc_krw"] = per_slot
                        df_live["shares"] = (df_live["alloc_krw"] / df_live["close"]).astype(int)
                        df_live["stop_price"] = (df_live["close"] * (1 - df_live["stop_pct"])).round().astype(int)
                        df_live["target_price"] = (df_live["close"] * (1 + df_live["target_pct"])).round().astype(int)
                        return today_regime, regime, df_live, "live"
                    return today_regime, regime, pd.DataFrame(), "live"
        except Exception as e:  # noqa: BLE001
            st.info(f"실시간 데이터 수집 실패 ({type(e).__name__}). 가장 최근 캐시된 시그널을 사용합니다.")

    # --- Cache fallback path (used on Cloud or when live fails) ---
    df_cached, fname = _load_latest_cached_signals()
    regime = _load_cached_regime_series()
    today_regime = bool(regime.iloc[-1]) if not regime.empty else None
    if df_cached.empty:
        return today_regime, regime, pd.DataFrame(), "none"

    # Recompute sizing using the user-specified capital + top_n
    df_cached = df_cached.head(top_n).copy()
    per_slot = capital / top_n
    df_cached["alloc_krw"] = per_slot
    if "close" in df_cached.columns:
        df_cached["shares"] = (df_cached["alloc_krw"] / df_cached["close"]).astype(int)
        df_cached["stop_price"] = (df_cached["close"] * (1 - df_cached.get("stop_pct", 0.03))).round().astype(int)
        df_cached["target_price"] = (df_cached["close"] * (1 + df_cached.get("target_pct", 0.15))).round().astype(int)
    return today_regime, regime, df_cached, f"cache:{fname}"


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
    ["Today's Signals", "Performance", "Trades", "Strategy Stats", "Config"],
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
    st.caption(f"KR Short-term Trading · {datetime.today().strftime('%Y-%m-%d %H:%M')}")
with hdr2:
    summary = load_final_summary()
    if not summary.empty and "Momentum5_tuned" in summary.index:
        cagr = summary.loc["Momentum5_tuned", "CAGR"] * 100
        st.metric("Backtest CAGR", f"{cagr:.1f}%")
with hdr3:
    if not summary.empty and "Momentum5_tuned" in summary.index:
        sh = summary.loc["Momentum5_tuned", "Sharpe"]
        st.metric("Sharpe", f"{sh:.2f}")

st.divider()


# ============================================================
# Page: Today's Signals
# ============================================================
if page == "Today's Signals":
    st.subheader("📅 Today's Entry Candidates")

    with st.spinner("Loading signals..."):
        regime_today, regime_series, sig_df, source = compute_today_signals(
            capital=capital, top_n=top_n, force=force_refresh, allow_live=allow_live
        )

    # Source badge
    if source.startswith("cache:"):
        fname = source.split(":", 1)[1]
        # Extract YYYY-MM-DD from filename
        st.caption(f"📦 캐시된 시그널 사용 — `{fname}`")
    elif source == "live":
        st.caption("🟢 실시간 데이터")
    elif source == "none":
        st.warning("저장된 시그널이 없습니다. 로컬에서 `edith` 명령을 실행해 시그널을 생성하고 GitHub에 push하세요.")
        st.stop()

    if regime_series is None or (isinstance(regime_series, pd.Series) and regime_series.empty):
        if sig_df is None or sig_df.empty:
            st.error("시그널 데이터 없음. `edith` 실행 후 push 필요.")
            st.stop()
        regime_today = True  # assume bullish if signal exists in cache (gate already applied)

    c1, c2, c3 = st.columns(3)
    with c1:
        if regime_today:
            st.success("🟢 **KOSPI Regime: BULLISH**\n\nNew entries allowed.")
        else:
            st.warning("🔴 **KOSPI Regime: BEARISH**\n\nNo new entries today.")
    with c2:
        on_days = int(regime_series.iloc[-60:].sum())
        st.metric("Regime ON (last 60d)", f"{on_days}/60")
    with c3:
        st.metric("Capital / slot", f"{capital/top_n:,.0f} KRW")

    st.markdown("##### KOSPI Regime — last 60 days")
    last60 = regime_series.iloc[-60:].astype(int)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=last60.index, y=last60.values,
        mode="lines", fill="tozeroy", line=dict(color="#3b82f6", width=1.5),
        hovertemplate="%{x|%Y-%m-%d}: %{y}<extra></extra>",
    ))
    fig.update_layout(
        height=120, margin=dict(l=10, r=10, t=10, b=10),
        yaxis=dict(tickvals=[0, 1], ticktext=["OFF", "ON"], range=[-0.1, 1.1]),
        xaxis_title="", showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    if not regime_today:
        st.info("Regime gate is OFF — strategy is dormant. Existing positions still managed manually.")
        st.stop()

    if sig_df is None or sig_df.empty:
        st.info("No entry signals today even though regime is bullish.")
        st.stop()

    st.markdown(f"##### {len(sig_df)} entry candidate(s) — order at next session's OPEN")

    show = sig_df.copy()
    show["close"] = show["close"].map(lambda x: f"{x:,.0f}")
    show["ret5d"] = show["ret5d"].map(lambda x: f"{x*100:.2f}%")
    show["alloc_krw"] = show["alloc_krw"].map(lambda x: f"{x:,.0f}")
    show["stop_price"] = show["stop_price"].map(lambda x: f"{x:,}")
    show["target_price"] = show["target_price"].map(lambda x: f"{x:,}")
    show = show[[
        "code", "name", "board", "close", "ret5d",
        "shares", "alloc_krw", "stop_price", "target_price", "max_hold",
    ]]
    show.columns = ["코드", "종목명", "시장", "종가", "5일수익률",
                    "매수주수", "배분(KRW)", "손절가", "목표가", "최대보유일"]
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.caption(
        f"진입: 다음 거래일 시가 매수.  손절 -{FINAL_PARAMS_MOMENTUM['stop_pct']*100:.0f}% / "
        f"목표 +{FINAL_PARAMS_MOMENTUM['target_pct']*100:.0f}% / "
        f"최대 보유 {FINAL_PARAMS_MOMENTUM['max_hold']}거래일."
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

**상단 3개 카드:**
- **KOSPI Regime**: 🟢 BULLISH면 신규 매수 허용, 🔴 BEARISH면 모든 신규 매수 금지 (보유분은 룰대로 관리)
- **Regime ON (last 60d)**: 최근 60거래일 중 매수 가능 일수. 너무 적으면 (예: <10) 한동안 약세장이었다는 뜻
- **Capital / slot**: 1포지션당 배분되는 금액 (좌측 사이드바 Capital ÷ Max positions)

**KOSPI Regime 차트:** 최근 60일간 매일 매수 허용 여부 (1=ON / 0=OFF). 약세장 진입/탈출 시점 파악용.

**후보 종목 표 컬럼:**
- `5일수익률`: 최근 5거래일 가격 변화. 큰 순서로 정렬됨. 진입 우선순위 기준.
- `매수주수`: 다음날 시가에 실제로 발주할 주식 수
- `손절가` / `목표가`: 매수 직후 HTS에 OCO 예약 등록할 가격 (각각 -3% / +15%)
- `최대보유일`: 5거래일 안에 손절/익절 안 맞으면 그날 종가 시장가 청산

**중요한 가정:** 실제 진입은 **다음 거래일 09:00 시가**입니다. 갭 상승(+5% 이상)이면 손절폭이 커지므로 그 종목은 스킵 권장.

자세한 운영 절차는 [OPS.md](OPS.md) 참조.
""")


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
    default = [o for o in ["Momentum5_tuned", "KOSPI B&H"] if o in options]
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
**Performance** 페이지는 **2020-01 ~ 현재까지의 전체 백테스트 결과**를 보여줍니다.

**Equity Curve (위 차트):**
- Y축 = 초기 자본 대비 배수. 1.0에서 시작.
- `Momentum5_tuned`가 메인 전략 (실제 운용용)
- `NewHigh52w_tuned`는 대안 전략, `Ensemble_M+NH`는 둘 합친 버전 — 참고용
- `KOSPI B&H`는 KOSPI 지수를 사서 묻어두기만 한 경우. 우리 전략과의 alpha를 한눈에 비교

**Drawdown 차트:** 각 시점에서 직전 고점 대비 얼마나 빠졌는지(%). 0이 고점 갱신 중, 음수가 깊을수록 아픈 시기. **MDD(최대 손실폭)** = 차트의 가장 깊은 골짜기.

**Summary table 주요 컬럼:**
- `CAGR`: 연복리 수익률
- `Sharpe`: 위험 대비 수익 (1 이상이면 우수, 2 이상은 매우 우수)
- `Sortino`: 하방 변동성만 고려한 Sharpe
- `Calmar`: CAGR ÷ |MDD|. 손실 1% 감당할 때마다 얻는 수익
- `MDD`: 백테스트 기간 최대 손실폭 (음수)
- `ProfitFactor`: 총 이익 ÷ 총 손실 (>1이면 흑자)

**Yearly returns:** 각 전략의 연도별 수익률 히트맵. 초록=수익 / 빨강=손실. **2022년 EDITH가 0%인 건 KOSPI 레짐 OFF로 거래 자체를 안 했기 때문 — 이게 약세장 방어의 핵심 메커니즘.**
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

    strat = st.selectbox("Strategy", options=list(trades_map.keys()))
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

**상단 4개 카드:**
- `Trades`: 백테스트 전체 기간 거래 수 (Momentum5는 약 1,500건)
- `Win rate`: 익절·시간청산 중 수익으로 끝난 비율 (모멘텀 전략 특성상 30% 내외, 정상)
- `Avg P&L`: 거래 1건당 평균 손익률. 0보다 크면 흑자 시스템
- `Total P&L`: 모든 거래 손익률 단순 합산 (참고용)

**Exit reasons 도넛:**
- `stop`: -3% 손절로 끝난 거래 (가장 많음, 70%+가 정상)
- `target`: +15% 익절로 끝난 거래 (전체의 ~15%, 적지만 큰 수익)
- `time`: 5거래일 만기로 끝난 거래 (나머지)

> 모멘텀 전략의 핵심: **손절은 많고 작게, 익절은 적고 크게**. 비대칭 R:R로 흑자 유지.

**P&L distribution 히스토그램:** 거래 1건당 손익률 분포. -3%에 큰 막대(손절), +15% 부근에 작은 막대(익절), 가운데에 시간청산 분포가 보임.

**Recent trades 표:**
- `entry_date` / `exit_date`: 매수일 / 매도일
- `reason`: stop / target / time 중 하나
- `pnl_pct`: 수수료·세금·슬리피지 모두 차감 후 순손익률
""")


# ============================================================
# Page: Strategy Stats
# ============================================================
elif page == "Strategy Stats":
    st.subheader("🎯 Strategy Stats — Momentum5_tuned")
    trades_map = load_final_trades()
    df = trades_map.get("Momentum5_tuned")
    if df is None:
        st.warning("Trades CSV not found.")
        st.stop()

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
    fig_h = px.histogram(df, x="hold_days", nbins=10, title="Hold days distribution")
    fig_h.update_layout(height=260, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig_h, use_container_width=True)

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Strategy Stats** 페이지는 **Momentum5_tuned (메인 전략) 한정**으로 패턴 분석을 보여줍니다.

**Monthly P&L 막대그래프:**
- 각 월의 모든 거래 손익률을 단순 합산. 초록=수익월, 빨강=손실월.
- 한 종목당 200만원 × 약 1.5건/월 매매 가정하면, 막대 1% ≈ 약 3만원 실손익 규모.
- 연속 빨강 막대가 3개월 이상이면 **전략이 깨졌는지 점검 시그널**.

**Yearly trade stats 표:**
- `Trades`: 그 해의 매매 횟수. 0이면 KOSPI 레짐이 거의 OFF였던 해 (예: 2022).
- `WinRate`: 그 해 승률. 25~35% 사이가 정상.
- `Sum P&L`: 그 해 모든 거래 손익률 합. 50% 이상이면 강세장 + 좋은 시그널 매칭.

**Hold days distribution:**
- 매매 1건이 며칠 만에 끝났는지 분포. 대부분 1-2일 (손절 빨리 맞음).
- 5일 막대가 크면 시간청산이 많다는 뜻 (목표가 도달은 못 했지만 손절도 안 맞아 횡보).
""")


# ============================================================
# Page: Config
# ============================================================
elif page == "Config":
    st.subheader("⚙️ Strategy Configuration")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Entry conditions (all must be true)**")
        st.markdown(f"""
- 5-day return ≥ **{FINAL_PARAMS_MOMENTUM['ret_thresh']*100:.0f}%**
- Close > 20-day SMA (uptrend confirm)
- Volume > 20-day Volume MA (interest confirm)
- KOSPI regime ON (Close > SMA200 & SMA50 rising)
""")
        st.markdown("**Exit rules (first-touch wins)**")
        st.markdown(f"""
- Stop-loss: −**{FINAL_PARAMS_MOMENTUM['stop_pct']*100:.0f}%** (intraday low touch)
- Take-profit: +**{FINAL_PARAMS_MOMENTUM['target_pct']*100:.0f}%** (intraday high touch)
- Time exit: **{FINAL_PARAMS_MOMENTUM['max_hold']}** bars hold
""")

    with c2:
        st.markdown("**Universe**")
        st.markdown(f"""
- KOSPI top **{N_KOSPI}** by market cap
- KOSDAQ top **{N_KOSDAQ}** by market cap
- ETF / SPAC / preferred excluded
""")
        st.markdown("**Cost model**")
        st.markdown("""
- Buy fee: 0.015%
- Sell fee: 0.015%
- Securities transaction tax (sell): 0.18%
- Slippage: 0.10% per side
- Round-trip ≈ 0.42%
""")
        st.markdown("**Sizing**")
        st.markdown(f"""
- Capital: configurable via sidebar
- Max positions: configurable (default 5)
- Allocation: equal weight per slot
""")

    st.divider()
    st.markdown("##### Files")
    st.code(f"""
Backtest:   ./venv/bin/python scripts/run_final.py
Tune:       ./venv/bin/python scripts/run_tuning.py
Signals:    ./venv/bin/python scripts/daily_signal.py --capital {capital:,.0f}
Dashboard:  ./venv/bin/streamlit run dashboard.py
    """)

    st.divider()
    with st.expander("ℹ️ 이 페이지는 어떤 자료인가요?"):
        st.markdown("""
**Config** 페이지는 **현재 운용 중인 전략의 모든 설정값**을 한눈에 보여줍니다.

**Entry conditions:** 매수 신호가 발생하기 위해 동시에 만족해야 하는 4가지 조건. 한 개라도 빠지면 신호 안 남.

**Exit rules:** 매수 후 청산 트리거. 셋 중 가장 먼저 도달하는 것으로 청산.

**Universe:** 매일 신호 산출 대상 종목 풀. KOSPI 시가총액 상위 100 + KOSDAQ 상위 50 = 총 150종목. ETF, 스팩, 우선주는 자동 제외.

**Cost model:** 백테스트에 반영된 거래 비용. 실제 사용자 계좌의 수수료가 더 우대(예: 0.0036%)면 백테스트는 보수적 → 실제 수익이 더 클 가능성.

**Sizing:** 자본을 어떻게 나눠 매수하는지. 사이드바에서 변경하면 즉시 반영.

**Files 박스:** 터미널에서 실행할 명령어. 자세한 운영은 [OPS.md](OPS.md), 빠른 단축어는 `edith` / `edith-ui` (`~/.zshrc` 참조).
""")
