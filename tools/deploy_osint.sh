#!/bin/sh
# gh 로그인 후 실행하면 저장소 생성·푸시·시크릿 등록까지 한 번에 끝난다.
# 시크릿 값은 환경변수에서 직접 읽어 넘긴다(화면에 출력하지 않는다).
set -e
REPO="${1:-webpaper-osint}"
DIR=/var/minis/shared/osint-gh

gh auth status >/dev/null 2>&1 || { echo "먼저 gh auth login 을 하세요"; exit 1; }

cd "$DIR"
echo "== 저장소 생성 =="
if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "이미 있음: $REPO"
else
  gh repo create "$REPO" --public --source=. --remote=origin \
     --description "WebPaper OSINT bot — RSS 수집·비트 균형·LLM 요약 → 텔레그램 채널" --push
fi

echo "== 원격 확인 =="
git remote -v | head -2

echo "== 시크릿 등록 =="
for v in TELEGRAM_BOT_TOKEN GEMINI_API_KEY GROQ_API_KEY CEREBRAS_API_KEY MISTRAL_API_KEY OPENROUTER_API_KEY; do
  val=$(eval "printf '%s' \"\${$v:-}\"")
  if [ -n "$val" ]; then
    gh secret set "$v" --body "$val" -R "$(gh repo view --json nameWithOwner -q .nameWithOwner)" >/dev/null
    echo "  $v 등록"
  else
    echo "  $v 없음(건너뜀)"
  fi
done

echo "== 워크플로 권한(쓰기) 확인은 웹에서 =="
gh repo view --json url -q .url
