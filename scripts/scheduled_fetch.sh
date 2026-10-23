#!/bin/bash
# 예약/정기 조사 실행용(systemd user timer 등에서 호출).
#   scripts/scheduled_fetch.sh [연도]   (기본: 올해)
# OpenAlex API 키(OPENALEX_API_KEY)는 호출하는 쪽이 환경에 넣어 둔다
# (예: systemd 서비스에서 bash -ic 로 ~/.bashrc를 읽어 실행).
# 로그는 data/fetch_<날짜_시각>-scheduled.log 에 남기고, 키 값은 ***로 가린다.
set -u
cd "$(dirname "$0")/.." || exit 1
YEAR="${1:-$(date +%Y)}"
mkdir -p data
LOG="data/fetch_$(date +%F_%H%M)-scheduled.log"
if [ -z "${OPENALEX_API_KEY:-}" ]; then
    echo "[scheduled] WARN OPENALEX_API_KEY 없음 - OpenAlex는 무료 공유 한도로 동작" >> "$LOG"
fi
python3 main.py fetch --year "$YEAR" >> "$LOG" 2>&1
status=$?
sed -i -E 's/(api_key=)[^&[:space:]]+/\1***/g' "$LOG"
echo "[scheduled] 종료 코드 $status ($(date '+%F %T'))" >> "$LOG"
exit $status
