# WebPaper OSINT bot

@webpaperHQ 텔레그램 채널에 뉴스를 보도하는 봇. 폰(nanoMuse 샌드박스)에서 돌던 것을
GitHub Actions 로 옮긴 버전이다. 폰이 꺼지거나 재부팅돼도 계속 돈다.

## 어떻게 도나

```
GitHub 예약(*/5분)  →  run_once.py  →  한 회차
   1. state/posted.jsonl → in-memory SQLite (최근 5일 게시 이력)
   2. sources.txt 96개 중 이번 샤드(1/2)만 수집        ← 피드당 10분 주기
   3. beats.classify_pending()  비트 분류(LLM 1콜)
   4. osint.llm_publish()  비트·지역 균형 선택 → 본문 추출 → 요약 → 텔레그램 전송
   5. 게시했으면 상태 1줄 append → 즉시 커밋
```

- 게시 상한 시간당 10건, 점수 하한 11.5 (`config.json`)
- 회차당 소요: 실측 5~20초 (GitHub 러너)
- **비트 균형 레인이 비면** 점수 상위에서 필터 통과 첫 건을 쓰는 안전망이 있다
  (`install_loose_pick`). 없으면 후보가 없어 채널이 조용히 멈춘다.

## 트리거가 두 겹인 이유

이 계정에서 GitHub 예약(cron)은 **새 저장소 등록 직후 약 90분간 발화하지 않았다.**
(같은 설정의 깨끗한 테스트 저장소도 16분간 0회 → 저장소 문제가 아니라 계정/등록 지연)
그래서 예약이 살아난 뒤에도 다음 두 가지를 함께 둔다.

실측: 이 저장소의 `*/5` 예약은 30~39분 간격으로만 떴다. 그래서 주 트리거를
cron-job.org(5분)로 옮겼고, GitHub 예약은 보조로 남겼다. 한 회차는 20~50초짜리 단발이다.
비상시에는 `osint-block.yml` 을 수동 실행해 5시간 30분을 채운다(잡 타임아웃 355분).

| 트리거 | 주기 | 역할 |
|---|---|---|
| GitHub `schedule` | 명목 5분(실측 수십 분) | 보조. 외부 크론이 죽었을 때만 의미 |
| cron-job.org | 5분 | 주 트리거 |
| `gh workflow run osint-block.yml` | 수동 | 예약이 며칠 죽었을 때 5시간 30분을 한 번에 채움 (`timeout 355분`) |

주의: 블록을 계속 돌리면 러너를 하루 종일 쓰는 셈이라 GitHub 의 "프로젝트와 무관한
연산" 금지 조항과는 거리가 있다. 문제가 되면 블록 길이를 줄이고 주기를 늘리거나
VPS 로 옮기는 게 맞다.

## 최초 설정 (한 번만)

1. 저장소는 Public (공개 저장소는 Actions 무료·무제한).
2. **Settings → Secrets and variables → Actions** 에 등록:

   | 이름 | 값 |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | 봇 토큰 |
   | `GEMINI_API_KEY` | 요약 1순위 |
   | `GROQ_API_KEY` | 요약 2순위 |
   | `CEREBRAS_API_KEY` · `MISTRAL_API_KEY` · `OPENROUTER_API_KEY` | (선택) 폴백 |

3. **Settings → Actions → General → Workflow permissions** = *Read and write*
   (상태 커밋에 필요)

## 문제 생겼을 때

| 증상 | 볼 것 |
|---|---|
| 채널 침묵 | Actions 탭 최근 run. 초록인데 "게시 없음"이면 후보가 필터에 걸린 것 |
| 예약 실행이 사라짐 | 저장소 활동이 60일 없으면 공개 저장소 예약이 자동 비활성. `keepalive.yml` 이 매주 커밋해 막는다 |
| `llm: 실패` | LLM 키 한도. 다른 키를 secret 에 추가하면 폴백 |
| 중복 게시 | 폰 쪽 `/var/minis/shared/osint` 데몬이 살아 있는지 확인 (`pgrep -f osint.py`) |

## 파일

- `run_once.py` — 회차 실행기(상태 복원·저장, 샤드 수집, 자율 루프)
- `osint.py` — 수집·채점·선택·전송 본체
- `beats.py` — 비트 분류와 z점수·쿼터 배분
- `llm.py` — Gemini→Mistral→Groq→OpenRouter 폴백 요약
- `writer.py` — 본문 추출(JSON-LD·문단), 구글뉴스 URL 해석, 해요체 변환
- `news_fetch.py` — RSS/Atom 파서
- `sources.txt` — 96개 소스 (`분류|이름|URL`)
