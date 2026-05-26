#!/bin/bash
# EDITH daily auto-commit
# - Runs the daily signal script
# - Commits results/signals_YYYY-MM-DD.csv to the local git repo
# - Pushes to origin (if remote configured)
#
# Mirrors JARVIS's run_and_upload.sh pattern.

set -e
cd "$(dirname "$0")/.."

# Load KRX_ID / KRX_PW (needed by pykrx investor-flow endpoint).
# ~/.zshrc has them exported; sourcing here propagates to this bash process
# even when invoked from a non-interactive parent (cron, CI, GUI launchers).
if [ -f "$HOME/.zshrc" ]; then
    # shellcheck disable=SC1090
    set +e  # don't let unrelated lines (aliases, completions) kill us
    source "$HOME/.zshrc" 2>/dev/null
    set -e
fi

CAPITAL=${EDITH_CAPITAL:-10000000}
TODAY=$(date '+%Y-%m-%d')

if [ -z "${KRX_ID:-}" ] || [ -z "${KRX_PW:-}" ]; then
    echo "⚠️  KRX_ID/KRX_PW 환경변수 미설정 — 외인 매수 booster가 비활성화됩니다."
fi

echo "🦸 EDITH Step 1/4: 시그널 산출 (capital=${CAPITAL})"
./venv/bin/python scripts/daily_signal.py --capital "${CAPITAL}"

echo ""
echo "📸 Step 2/4: 클라우드용 캐시 스냅샷 (regime, KOSPI B&H)"
./venv/bin/python scripts/snapshot_for_cloud.py

echo ""
echo "📦 Step 3/4: 결과 파일 git add"
git add results/signals_${TODAY}.csv 2>/dev/null || true
git add results/final_summary.csv 2>/dev/null || true
git add results/regime_series.csv results/kospi_buyhold.csv 2>/dev/null || true

if git diff --cached --quiet; then
    echo "  (커밋할 변경 없음)"
else
    git commit -m "Daily signals: ${TODAY}"
    echo ""
    echo "☁️  Step 4/4: GitHub push"
    if git remote get-url origin >/dev/null 2>&1; then
        git pull --rebase origin main 2>/dev/null || true
        git push origin main
    else
        echo "  (origin 미설정 — 로컬 커밋만 완료)"
    fi
fi

echo ""
echo "✓ EDITH 일일 작업 완료 (${TODAY})"
