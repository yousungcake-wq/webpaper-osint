# 보안 모델

이 저장소는 **공개**다. 코드·소스 목록·게시 이력은 누구나 볼 수 있다고 전제한다.
비밀은 전부 GitHub Actions Secrets 에만 있고, 저장소에는 어떤 키도 들어 있지 않다.

## 비밀의 위치

| 비밀 | 어디에 | 누가 볼 수 있나 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Actions secret | 저장소 관리자, 워크플로 실행(마스킹됨) |
| `GEMINI_API_KEY` · `GROQ_API_KEY` | Actions secret | 같음 |
| GitHub PAT (크론용) | cron-job.org 잡 헤더 | cron-job.org 계정 소유자 |
| `owner_chat` (개인 DM ID) | 폰의 `config.json` | 기기 소유자 |
| gh CLI 토큰 | 폰 샌드박스 `~/.config/gh` | 기기 소유자 |

## 이 구조에서 무엇이 가능한가

- **외부인이 저장소에 코드를 넣을 수 없다.** 워크플로 트리거는 `schedule` 과
  `workflow_dispatch` 뿐이고, `pull_request` 계열 이벤트를 쓰지 않는다.
  포크 PR 로 임의 코드를 실행시키는 경로가 없다.
- **크론용 PAT 의 권한은 `webpaper-osint` 1개 저장소의 Actions: write 뿐이다.**
  이 토큰이 새더라도 할 수 있는 일은 워크플로 실행/취소와 입력값 전달(선언된
  `dry_run`·`minutes`)까지다. 입력은 셸로 보간되지 않고 환경변수로만 쓰인다.
- **워크플로 토큰(`GITHUB_TOKEN`)은 이 저장소에만 쓰기 가능**하고, 실행마다 발급·폐기된다.

## 키가 새는 경우와 대응

| 상황 | 즉시 할 일 |
|---|---|
| `TELEGRAM_BOT_TOKEN` 노출 | BotFather → `/revoke` → 새 토큰을 secret 에 갱신 |
| LLM 키 노출 | 제공자 콘솔에서 폐기 후 secret 갱신 |
| 크론 PAT 노출 | GitHub → Settings → Developer settings → Fine-grained tokens → Revoke → cron-job.org 헤더 교체 |
| cron-job.org 계정 침해 | 비밀번호 변경 + 2단계 인증, 잡 삭제 후 재생성 |
| 폰 분실 | GitHub → Settings → Applications 에서 gh CLI 인증 취소 |

## 점검 주기

- 주간 점검 태스크가 `gh secret list` 개수와 실행 이력을 확인한다.
- cron-job.org 잡에 **실패 시 이메일 알림**을 켜 두면 PAT 만료·폐기를 바로 알 수 있다.
- 의존 액션은 커밋 SHA 로 고정했고, 갱신은 Dependabot 이 PR 로 올린다.
