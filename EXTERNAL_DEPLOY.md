# EDITH 외부 공개 가이드

EDITH를 **로컬에서만 보는 것을 넘어 외부에서도 접근 가능**하게 만드는 3가지 방법을 정리합니다.

| 방법 | 비용 | 어렵기 | 적합 사용처 |
|---|---|---|---|
| **A. GitHub 자동 업로드** | 무료 | 쉬움 | 매일 시그널 기록 보관 + 어디서나 열람 |
| **B. Streamlit Community Cloud** | 무료 | 보통 | 진짜 웹 대시보드 (남에게도 보여줄 수 있음) |
| **C. Cloudflare Tunnel** | 무료 | 보통 | 본인만 외부에서 접근 (인증 가능) |

권장 조합: **A + B**. JARVIS의 패턴과 동일.

---

## A. GitHub 자동 업로드 (먼저 셋업)

### A-1. GitHub repo 만들기

1. https://github.com/new 에서 새 repo 생성
   - Name: `EDITH` (또는 원하는 이름)
   - **Private 권장** (시그널은 본인 매매 신호이므로)
   - "Add README" 같은 옵션은 모두 **체크 해제** (이미 로컬에 있음)
2. 생성 직후 보이는 페이지에서 repo URL 복사. 예: `https://github.com/ehnahcle/EDITH.git`

### A-2. 로컬 repo 연결 + 첫 푸시

```bash
cd ~/Documents/EDITH

# 첫 커밋 (이미 git init은 되어 있음)
git commit -m "Initial EDITH commit"

# remote 연결 + 푸시
git branch -M main
git remote add origin https://github.com/<YOUR_GITHUB_ID>/EDITH.git
git push -u origin main
```

> macOS Keychain이 자격증명을 묻습니다. GitHub Personal Access Token이 필요할 수 있음 — https://github.com/settings/tokens 에서 `repo` 권한 토큰 발급.

### A-3. 매일 자동 업로드

**옵션 1: GitHub Actions cron (서버 측, 컴퓨터 꺼져 있어도 실행)**

이미 `.github/workflows/daily_signals.yml` 파일이 있습니다. push 후 자동으로 활성화됨.

매일 평일 **16:00 KST**에 GitHub 서버에서:
- FinanceDataReader로 OHLCV fetch
- pykrx로 외인·기관 일별 fetch (WEAK regime 시 booster용)
- `daily_signal.py` 실행
- `results/signals_YYYY-MM-DD.csv` 커밋 + push

**필수 GitHub repo 설정:**
1. **Settings → Secrets and variables → Actions → Secrets** 에 두 개 등록:
   - `KRX_ID` : data.krx.co.kr 회원ID
   - `KRX_PW` : 비밀번호
   - 없어도 워크플로는 동작하지만 외인 booster 비활성 → alpha 손실
2. **Settings → Variables → Actions** (선택)에 `EDITH_CAPITAL` 변수 추가하면 자본금 조정 가능 (기본 10,000,000)

> 한국 KRX 사이트가 외국 IP를 차단할 가능성이 있습니다. GitHub Actions(미국 IP)에서 pykrx 로그인이 실패할 수 있음 — 그 경우 daily_signal은 graceful fallback (booster 비활성, baseline 시그널만 생성). 완전 실패 시 옵션 2로 전환.

**옵션 2: 로컬 launchd (macOS, 본인 컴퓨터에서 매일 실행)**

```bash
# /Users/chanhui/Library/LaunchAgents/com.edith.signals.plist 생성
cat > ~/Library/LaunchAgents/com.edith.signals.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.edith.signals</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/chanhui/Documents/EDITH/scripts/auto_commit_signals.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>15</integer>
        <key>Minute</key><integer>35</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/edith_cron.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/edith_cron.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.edith.signals.plist
```

평일 15:35 KST에 자동 실행 (장 마감 5분 후). 컴퓨터가 켜져 있어야 함. 슬립 모드면 깨워서 실행.

---

## B. Streamlit Community Cloud (웹 대시보드 공개)

### B-1. 사전 준비
- GitHub repo가 **public**이어야 함 (Streamlit 무료 플랜 제약)
- 또는 Streamlit Cloud 결제 플랜으로 private 가능
- 매매 시그널이 외부 노출되도 OK여야 함

> Private이 필수면 C 옵션 (Cloudflare Tunnel) 권장.

### B-2. 배포

1. https://streamlit.io/cloud 에서 GitHub 계정으로 로그인
2. **New app** 클릭
3. 설정:
   - Repository: `<YOUR_GITHUB_ID>/EDITH`
   - Branch: `main`
   - Main file path: `dashboard.py`
   - Python version: `3.11`
4. **Deploy** 클릭. 2~3분 후 `https://<your-slug>.streamlit.app` 주소 발급

### B-3. 자동 재배포

GitHub에 push할 때마다 자동 재배포됨. 별도 설정 없음.

### B-4. 도메인 변경 (선택)
Streamlit Cloud → App Settings → Custom subdomain에서 변경 가능. 예: `https://edith-chanhui.streamlit.app`

### B-5. 인증 (선택)
- 무료 플랜: GitHub 로그인 기반 접근 제한 (특정 GitHub 계정만 접근)
- Streamlit Cloud → App Settings → Sharing → Viewer access에서 설정

---

## C. Cloudflare Tunnel (본인만 외부 접근)

로컬 컴퓨터의 EDITH(`localhost:8511`)를 안전한 외부 도메인으로 노출. **시그널을 깃에 공개하기 싫고, 본인이 외부에서만 보고 싶을 때.**

### C-1. cloudflared 설치
```bash
brew install cloudflared
```

### C-2. Cloudflare 계정 + 무료 도메인 (또는 본인 도메인) 준비
```bash
cloudflared tunnel login    # 브라우저 자동 오픈
cloudflared tunnel create edith
```

### C-3. 터널 실행
```bash
# 매번 수동 실행
cloudflared tunnel --url http://localhost:8511

# → 임시 도메인 발급 (예: https://random-words.trycloudflare.com)
```

### C-4. 영구 도메인 (선택)
Cloudflare DNS 설정 + `~/.cloudflared/config.yml`에 라우팅 추가. 자세한 가이드는 Cloudflare 공식 문서 참조.

### C-5. 인증 추가 (강력 권장)
Cloudflare Zero Trust → Access → Application 에서 이메일 OTP 인증 추가. 본인 이메일만 접근 가능.

---

## 추천 워크플로우

```
[매일 15:30 KST]   장 마감
   ↓
[매일 15:35 KST]   launchd가 auto_commit_signals.sh 실행
   ↓
                  - 시그널 산출 → results/signals_YYYY-MM-DD.csv
                  - 로컬 git commit + GitHub push
   ↓
[자동]             Streamlit Cloud 재배포 (1~2분)
   ↓
[15:40 KST]        어디서든 모바일에서 https://edith-xxx.streamlit.app 접속
   ↓
[다음날 09:00]     HTS에서 시가 매수 + OCO 예약 등록
```

---

## FAQ

**Q. Private repo + Streamlit Cloud 같이 쓰고 싶다.**
A. Streamlit Cloud는 무료 플랜에서 private repo 지원 제한적. 대안:
   - C 옵션 (Cloudflare Tunnel) — 본인만 접근
   - Vercel + FastAPI 같은 자체 호스팅

**Q. GitHub Actions가 KRX 차단으로 실패한다.**
A. 두 가지 영향이 분리됨:
   - FDR (OHLCV) 실패 → daily_signal 자체가 안 돌아감. 옵션 2 (launchd 로컬 자동 실행)로 전환.
   - pykrx (외인 데이터) 실패 → booster만 비활성, baseline 시그널은 정상 commit. 이 경우는 옵션 1 유지해도 됨.
   - 로그에서 `"booster disabled"` 메시지로 부분 실패 식별 가능.

**Q. KRX 키는 무료인가? 추가 비용 있나?**
A. 완전 무료. data.krx.co.kr 회원가입만 하면 됨. 데이터 조회 자체에 quota나 요금 없음. pykrx가 알아서 세션을 1시간마다 갱신.

**Q. 시그널이 외부에 노출되면 알파 사라질까?**
A. 모멘텀 전략은 본질적으로 따라하기 어려움 (1주일 이내 청산, 종목 자주 바뀜). 다만 본인이 운용 중인 종목까지 공개되는 게 부담스러우면 Private repo + 로컬 실행 권장.
