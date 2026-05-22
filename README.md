# EDITH — Even Dead, I'm The Hero

KOSPI·KOSDAQ Top 150 종목 대상 **단기 모멘텀 자동 시그널 시스템**.
백테스트 + Walk-Forward 검증을 거친 전략을 매일 실행하여 다음 거래일 매수 후보를 출력합니다.

> 자매 프로젝트: [JARVIS](https://github.com/ehnahcle/Quant-Tool) (미국 시장)

---

## Backtest 요약 (2020-01 ~ 2026-05, 150 종목)

| Strategy | CAGR | Sharpe | MDD | PF | WR |
|---|---:|---:|---:|---:|---:|
| **Momentum5_tuned (메인)** | **43.1%** | **1.22** | -27.5% | 1.34 | 29.3% |
| KOSPI Buy & Hold | 20.7% | 0.94 | -35.7% | — | — |

- IS(2020-23) Sharpe 0.90 → OOS(2024-26.5) Sharpe 1.70 (과적합 신호 없음)
- 2022년 KOSPI -25% 시기 거래 0건 (KOSPI 레짐 필터로 자동 회피)

자세한 백테스트는 `Performance` 페이지 또는 [results/final_summary.csv](results/final_summary.csv) 참조.

---

## 전략 한 줄 요약

> **"5일 동안 10% 이상 오른 KOSPI/KOSDAQ Top150 종목을 다음날 시가에 매수, 손절 -3% / 익절 +15% / 5거래일 시간청산. KOSPI 레짐이 강세일 때만 매수."**

자세한 진입·청산·운영 매뉴얼은 [OPS.md](OPS.md) 참조.

---

## Quick Start

```bash
# 0. 의존성 설치 (최초 1회)
cd ~/Documents/EDITH
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 1. 매일 시그널 산출 (장 마감 후)
./venv/bin/python scripts/daily_signal.py --capital 10000000

# 2. 대시보드 (브라우저)
./start_dashboard.sh    # http://localhost:8511

# 3. 백테스트 재현
./venv/bin/python scripts/run_final.py

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
├── edith/                    # 코어 라이브러리
│   ├── data_loader.py        # FinanceDataReader + pykrx 데이터 수집
│   ├── backtest.py           # 백테스트 엔진 (수수료/세금/슬리피지 반영)
│   ├── regime.py             # KOSPI 시장 레짐 필터
│   ├── metrics.py            # 성과 지표 (Sharpe, Sortino, MDD 등)
│   ├── final_strategy.py     # 운용 중 전략 (Momentum5_tuned)
│   └── strategies/           # 검증 후보 7개 전략
├── scripts/
│   ├── daily_signal.py       # 매일 시그널 생성 (실전용)
│   ├── run_final.py          # 백테스트 재현
│   ├── run_tuning.py         # 그리드 서치 + walk-forward
│   ├── run_all_backtests.py  # 7개 전략 비교
│   ├── run_v2_with_regime.py # 레짐 필터 효과 검증
│   └── auto_commit_signals.sh # 시그널 자동 GitHub 업로드
├── results/                  # 백테스트 결과 CSV + 일별 signals
├── dashboard.py              # Streamlit 대시보드 (5 페이지)
├── OPS.md                    # 실전 운영 매뉴얼
├── start_dashboard.sh        # 대시보드 실행 (포트 8511)
└── stop_dashboard.sh         # 대시보드 종료
```

---

## License & Disclaimer

개인 운용용 도구. 본 코드 / 결과는 **투자 자문이 아님**. 모든 매매는 본인 책임 하에 진행.
