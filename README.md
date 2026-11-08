# WebPaper OSINT bot

@webpaperHQ 텔레그램 채널에 뉴스를 보도하는 봇. 폰(nanoMuse 샌드박스)에서 돌던 것을
GitHub Actions 로 옮긴 버전이다. 폰이 꺼지거나 재부팅돼도 계속 돈다.

## 어떻게 도나

```
cron(*/5분, 1분 오프셋)
  └ run_once.py
      1. state/posted.jsonl  → in-memory SQLite (최근 5일 게시 이력)
      2. sources.txt 96개 중 이번 샤드(1/3)만 수집        ← 피드당 15분 주기
      3. beats.classify_pending()  비트 분류(LLM 1콜)
      4. osint.llm_publish()       비트·지역 균형 선택 → 본문 추출 → 요약 → 전송
      5. 게시했으면 상태 1줄 append
  └ 변경 있으면 state/ 커밋 (게시할 때만 발생, 하루 60~190회)
```

- 게시 상한: 시간당 10건. `config.json`
- 점수 하한: 11.5. `config.json`
- 회차당 소요: 실측 50~70초

## 최초 설정 (한 번만)

1. **저장소 만들기** — Public 권장(공개 저장소는 Actions 무료·무제한).
2. **Settings → Secrets and variables → Actions → New repository secret** 에 아래를 넣는다.

   | 이름 | 값 |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | 기존 봇 토큰 (폰에 설정된 것과 동일) |
   | `GEMINI_API_KEY` | 폰에 설정된 값 |
   | `GROQ_API_KEY` | 폰에 설정된 값 |
   | `CEREBRAS_API_KEY` | (선택) |
   | `MISTRAL_API_KEY` | (선택) |
   | `OPENROUTER_API_KEY` | (선택) |

   LLM은 위 순서로 폴백한다. 하나만 있어도 돌고, 많을수록 한도에 안 걸린다.
3. **Settings → Actions → General → Workflow permissions** 를
   *Read and write permissions* 로 바꾼다. (상태 커밋에 필요)
4. **Actions 탭 → OSINT Telegram bot → Run workflow** 로 한 번 수동 실행.
   - `dry_run` 체크하면 채널에 보내지 않고 로그에만 본문을 찍는다. 처음엔 이걸로 확인.
5. 정상이면 그대로 두면 5분마다 돈다.

## 폰 봇 끄기 (중요)

같은 채널에 두 곳이 쓰면 중복 게시된다. 이전 후 폰에서는
`/var/minis/shared/osint` 의 데몬과 `OSINT 감시봇 유지` 스케줄 태스크를 멈춘다.

## 문제 생겼을 때

| 증상 | 볼 것 |
|---|---|
| 채널 침묵 | Actions 탭의 최근 run 로그. 초록인데 게시가 없으면 "이번 회차 게시 없음" — 후보가 필터에 걸린 것 |
| `llm: 실패` | LLM 키 한도. 다른 키를 secret 에 추가하면 폴백 |
| 게시가 몰림 | GitHub cron 은 정각 부하 시 5~20분 지연될 수 있다. 정상 동작 |
| 스케줄이 사라짐 | 공개 저장소 60일 무활동 시 자동 비활성. `keepalive.yml` 이 매주 커밋해 막는다 |

## 파일

- `run_once.py` — 회차 실행기(상태 복원·저장, 샤드 수집)
- `osint.py` — 수집·채점·선택·전송 본체 (경로만 저장소 기준으로 수정)
- `beats.py` — 비트 분류와 z점수·쿼터 배분
- `llm.py` — Gemini→Mistral→Groq→OpenRouter 폴백 요약
- `writer.py` — 본문 추출(JSON-LD·문단), 구글뉴스 URL 해석, 해요체 변환
- `news_fetch.py` — RSS/Atom 파서
- `sources.txt` — 96개 소스 (`분류|이름|URL`)
