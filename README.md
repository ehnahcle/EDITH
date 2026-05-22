# EDITH — Even Dead, I'm The Hero

KOSPI·KOSDAQ Top 150 종목 대상 **단기 모멘텀 자동 시그널 시스템**.
백테스트 + Walk-Forward 검증을 거친 전략을 매일 실행하여 다음 거래일 매수 후보를 출력합니다.

> 자매 프로젝트: [JARVIS](https://github.com/ehnahcle/Quant-Tool) (미국 시장)

---

## Backtest 요약 (2010-01 ~ 2024-12, 150 종목, walk-forward)

| Strategy | CAGR | Sharpe | MDD | 거래수 |
|---|---:|---:|---:|---:|
| **Triple-mode + 외인 booster (운용중)** | **20.6%** | **0.96** | -27.4% | 1,340 |
| Triple-mode baseline (외인 booster 전) | 14.6% | 0.72 | -27.7% | 1,366 |
| Legacy Momentum5 단독 | 1.8% | 0.05 | -75.0% | — |
| KOSPI Buy & Hold | 2.3% | 0.23 | -43.9% | — |

| 기간 | Triple+booster Sharpe | Triple baseline Sharpe |
|---|---:|---:|
| IS 2010-2018 (박스권) | 0.78 | 0.66 |
| OOS_A 2019-2023 | **1.14** | 0.88 |
| OOS_B 2024 (KOSPI -10%) | **0.86** | 0.34 |

- 14년 walk-forward, 모든 기간에서 booster 추가가 baseline 상회 → 과적합 아님
- 2022년 KOSPI -25% 시기 거래 0건 (KOSPI 레짐 필터로 자동 회피)
- 외인 booster는 **WEAK 박스권 regime에서만 활성** (강세장에는 적용 시 오히려 악화)
- 자세한 결과: [results/foreign_booster_summary.csv](results/foreign_booster_summary.csv)

---

## 전략 한 줄 요약

> **KOSPI 레짐에 따라 3가지 sub-strategy를 자동 dispatch:**
> - 🟢 **STRONG_BULL** → Momentum5 (5일 +10%, stop -3% / target +15% / 7d)
> - 🟡 **WEAK** → NewHigh52w (52주 신고가, stop -7% / target +20% / 10d) + 외국인 5일 누적 순매수 z-score score booster
> - 🔴 **BEAR** → Disparity (-10% SMA20, stop -3% / target +10% / 3d)

자세한 진입·청산·운영 매뉴얼은 [OPS.md](OPS.md) 참조.

---

## Quick Start

```bash
# 0. 의존성 설치 (최초 1회)
cd ~/Documents/EDITH
python3 -m venv venv
./venv/bin/pip install -r requirements-full.txt

# 0-1. KRX 무료 계정 등록 → 외인 booster용 자격증명 (최초 1회)
#      https://data.krx.co.kr 회원가입 후 ~/.zshrc 에:
#        export KRX_ID='회원ID'
#        export KRX_PW='비밀번호'
#      source ~/.zshrc
#      자격증명 없어도 daily_signal은 graceful fallback (booster만 비활성)

# 0-2. 외인 데이터 backfill (최초 1회, ~50분, 캐시 16MB)
./venv/bin/python scripts/backfill_investor_flow.py

# 1. 매일 시그널 산출 (장 마감 후)
./venv/bin/python scripts/daily_signal.py --capital 10000000

# 2. 대시보드 (브라우저)
./start_dashboard.sh    # http://localhost:8511

# 3. 백테스트 재현
./venv/bin/python scripts/run_triple_mode_validation.py   # baseline
./venv/bin/python scripts/run_foreign_booster_validation.py   # +booster A/B

# 4. 분기 1회 파라미터 robust 재확인
./venv/bin/python scripts/run_tuning.py
```

zsh alias (`~/.zshrc`)를 설정하면:
```bash
edith         # 시그널 산출 + GitHub 자동 업로드
edith-ui      # 대시보드 시작
edith-test    # 백테스트 재현
edith-dir     # EDITH 디렉토리로 이동 + venv 활성화
```

---

## 프로젝트 구조

```
EDITH/
├── edith/                            # 코어 라이브러리
│   ├── data_loader.py                # FinanceDataReader (OHLCV, 무인증)
│   ├── investor_flow.py              # pykrx 외인·기관 일별 (KRX 계정 필요)
│   ├── backtest.py                   # 백테스트 엔진 (수수료/세금/슬리피지 반영)
│   ├── regime.py                     # KOSPI 3-tier 레짐 분류
│   ├── filters.py                    # 신호 필터 / score booster
│   ├── metrics.py                    # 성과 지표 (Sharpe, Sortino, MDD 등)
│   ├── final_strategy.py             # Triple-mode dispatcher + booster 훅
│   └── strategies/                   # 후보 7개 전략 (모듈별)
├── scripts/
│   ├── daily_signal.py               # 매일 시그널 생성 (실전용, booster 자동 적용)
│   ├── backfill_investor_flow.py     # KRX 외인 데이터 1회성 백필 (50분, 16MB)
│   ├── run_triple_mode_validation.py # 3-mode dispatcher 검증
│   ├── run_foreign_booster_validation.py # 외인 booster A/B 검증
│   ├── run_foreign_filter_validation.py  # (참고용, 폐기된 필터 접근)
│   ├── run_tuning.py                 # 그리드 서치 + walk-forward
│   ├── run_all_backtests.py          # 7개 전략 비교
│   └── auto_commit_signals.sh        # 시그널 자동 GitHub 업로드
├── data/cache_investor/              # 외인 일별 데이터 pickle 캐시 (16MB)
├── results/                          # 백테스트 결과 CSV + 일별 signals
├── dashboard.py                      # Streamlit 대시보드
├── OPS.md                            # 실전 운영 매뉴얼
├── start_dashboard.sh                # 대시보드 실행 (포트 8511)
└── stop_dashboard.sh                 # 대시보드 종료
```

---

## License & Disclaimer

개인 운용용 도구. 본 코드 / 결과는 **투자 자문이 아님**. 모든 매매는 본인 책임 하에 진행.
