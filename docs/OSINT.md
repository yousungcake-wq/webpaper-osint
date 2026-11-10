# OSINT 운영 문서 (2026-11-08, GitHub Actions 이전 후)

> 24시간 국제 뉴스 OSINT 봇. `@webpaperHQ` 텔레그램 채널에 보도한다.
> **2026-11-08 부로 폰(nanoMuse 샌드박스) 실행을 폐기하고 GitHub Actions 로 옮겼다.**
> 폰 시절 문서는 `docs/archive/phone-OSINT.md` 로 남겨 둔다.

## 구조

```
run_once.py          회차 실행기 (상태 복원 / 샤드 수집 / 자율 루프 / 상태 커밋)
osint.py             엔진: 수집·채점·선택·전송·감사 (collect/score_item/pick/make_draft/llm_publish/audit)
beats.py             비트 분류(TECH/FINANCE/CONFLICT/COMMODITY/ECONOMY/POLITICS) + z점수·쿼터·지역 균형
llm.py               무료 LLM 요약 (Gemini → Cerebras → Mistral → Groq → OpenRouter)
writer.py            본문 추출(JSON-LD·문단) · 구글뉴스 URL 해석 · 해요체 변환
news_fetch.py        RSS/Atom 파서 + Google News 쿼리
sources.txt          96개 소스 (`분류|이름|URL`)
state/posted.jsonl   게시 이력 (한 줄 = 1건, 약 220B, 최근 5일 유지)
.github/workflows/   osint.yml(5분) · keepalive.yml(주 1회)
```

## 실행 모델

```
GitHub 예약(*/5) → run_once.py
  1. state/posted.jsonl → in-memory SQLite (최근 5일)
  2. sources.txt 중 이번 샤드(1/2)만 수집 → collect()
  3. beats.classify_pending() : 비트 1차 키워드 + 2차 LLM 배치 분류
  4. osint.llm_publish() : 균형 선택 → 본문 추출 → 요약 → 텔레그램 전송
  5. 게시했으면 상태 1줄 append → 즉시 커밋
```

- 회차당 소요 5~20초. 시간당 게시 상한 10건, 최소 점수 11.5 (`config.json`).
- **SQLite 대신 JSONL**: 액션 러너는 매번 새 컨테이너라 상태를 커밋해야 하는데,
  바이너리 DB를 커밋하면 히스토리가 하루 수 GB로 불어난다. 대신 매 회차 in-memory
  SQLite에 되살려 기존 선택 로직을 **수정 없이** 재사용한다.
- **샤드 수집**: 96피드를 5분마다 다 긁으면 하루 27,648요청. 2등분해 번갈아 긁어
  피드당 10분 주기로 낮췄다.
- **루프 모드**: `LOOP_MINUTES` 가 있으면 그 시간 동안 5분 간격으로 계속 돈다.
  이 계정의 GitHub 예약이 5분을 안 지켜서(실측 39분 간격) 45분 블록으로 공백을 메운다.

### 트리거 3겹
| 트리거 | 주기 | 역할 |
|---|---|---|
| GitHub `schedule` | 명목 5분 / 실측 수십 분 | 블록을 갈아끼움 |
| 폰 스케줄 태스크 `OSINT 트리거(보조)` | 6회/일 (01:07 … 21:07) | 예약이 오래 죽었을 때 블록 재시동 |
| `gh workflow run osint-block.yml` | 수동 | 예약이 며칠 죽었을 때 5시간 30분 한 번에 |

⚠️ 러너를 하루 종일 쓰는 구조는 GitHub 의 "프로젝트와 무관한 연산" 조항과 거리가 있다.
문제가 되면 블록을 줄이고 주기를 늘리거나 VPS(월 $4~6)로 옮긴다.

### 동시성 — 실측으로 정정 (2026-11-10)
`concurrency: osint-cycle` + `cancel-in-progress: true` 의 실제 동작:

- **대기 중(pending) 회차끼리만 서로 취소된다.** 이미 실행 중인 잡은 선점되지 않는다.
- 블록이 도는 동안 크론 회차는 `pending` 으로 쌓였다가 5분마다 서로 취소되고, **블록이 끝나야** 시작한다.
  (05:32 블록 발사 → 05:35 회차 pending → 05:40 회차가 05:35 회차를 취소 → 05:44 블록 수동취소 직후 05:40 회차가 시작)
- 따라서 이전 주석의 "크론이 살아 있으면 5분 뒤 블록이 취소된다"는 **틀렸다.** 비상구는 중간에 죽지 않는다.
  대신 도는 동안 일반 회차는 멈춘다(블록이 같은 5분 주기로 게시하므로 채널 결과는 동일).
- 사고 방지 관점에서는 오히려 안전하다: 같은 시각에 두 프로세스가 게시하는 경로가 없다.

## 채점 (관심사 중심)

Gram 관심사: **국제분쟁 — 특히 아프리카·중동의 저보도 분쟁.** 전쟁 기사는 감점하지 않는다.

- 카테고리: conflict 5.0 > politics/economy/finance/tech 4.0 > google 1.5
- 전쟁 어휘: `airstrike`·`nuclear site`·`escalat`·`retaliat` +3, `war`·`bomb`·`blockade`·`mobiliz` +2
- 지역(THEATER_WORDS): 수단·다르푸르·RSF·사헬·M23·고마·티그라이 +3~4, 예멘·후티·가자·헤즈볼라 +3, 이란 +3
- 인도적(HUMAN_WORDS): famine·massacre·genocide·coup·junta·militia·war crime +3
- 감점: 스포츠·연예 −8 / 오피니언·리스트 −6 / 현지통화·로컬증시 −5 / 시세 나열 −9
- **저신호 외교 −고정**: `COURTESY`(조의·축하·생일)는 점수를 6.0으로 상한 처리해 탈락시킨다.
  (2026-11-08: "정상이 부친상 조의" 기사가 전쟁 어휘 중첩으로 33점 1위를 차지했다)
- 최근성: 4시간 창에서 최대 +4. 6시간 지난 기사는 폐기.

## 배분 — 2단 레인 + 지역 축

1. **우선 레인** — 자기 비트 안에서 z ≥ 4.0 이면 쿼터 무시 (같은 비트가 최근 5건 중 3건 이상이면 차단).
2. **균형 레인** — 목표비 부족분 순서. 단 그 비트 평균 이상(z ≥ 0.8)만.
   목표비 `TECH2·FINANCE2·CONFLICT2·ECONOMY2·COMMODITY1·POLITICS1` /
   지역 `EUROPE3·LATAM2·US2·ASIA2·AFRICA2·MIDDLE_EAST1`.
3. **z점수** — 비트마다 점수 분포가 달라서 `(score-평균)/표준편차` 로 정규화한다.
4. **안전망(`install_loose_pick`)** — 균형 레인이 비면 점수 상위 40건에서 필터 통과 첫 건을 쓴다.
   없으면 후보가 없어 조용히 침묵한다(폰 시절 45분 공백 사고의 원인).

## 중복 판정 — 4겹 (2026-11-09 강화)

한 겹으로는 계속 새는 구멍이 있었다. 실측으로 확인한 구멍마다 한 겹씩 막았다.

1. **URL 정규화** (`filters.canon_url`) — `?utm=`, `/amp`, 끝 슬래시, `www.` 차이를 같은 주소로 본다.
   이전엔 문자열 완전일치라 같은 글을 다른 주소로 받으면 그대로 통과했다.
   (`items.cu` 컬럼, 수집 단계와 후보 단계 양쪽에서 확인)
2. **제목 기반** `same_event()` — 제목 자카드 0.45 + 어간 축약 + 사상자 어휘 + 희귀토큰.
   - `toks()` 에 어간 축약(`_stem`: ing/ed/ies/ly/s) — kill/killed, strike/strikes, Houthi/Houthis 를 묶는다.
   - 판정: 자카드 > 0.45 **또는** 공통토큰 ≥ 4 **또는** 공통토큰 ≥ 3 & (사상자 어휘 | 자카드 ≥ 0.30)
     **또는** 희귀토큰(df ≤ 2%) 2개 이상.
3. **요약문 유사도** (`osint.dup_body`) — **생성된 한국어 요약**을 최근 72시간 게시분 250건과 비교.
   매체가 달라 영문 제목이 달라도 사건이 같으면 요약이 거의 같아진다.
   **임계값 0.18** (2026-11-10 재측정으로 0.22 → 0.18).
   - 2026-11-09 최초 설정 0.22는 채널 100건(4,950쌍) 표본에서 "0.20 이상은 3쌍"만 보고 정한 값이었다.
   - 2026-11-10 재측정(게시 198건 = 19,503쌍): **0.22 이상 0쌍 — 한 번도 발화하지 않았다.**
     실제 중복은 0.16~0.22 구간에 몰려 있었다: 프랑스 냉장고 해킹 5건 / 예멘 사나 공습 3건 /
     젠슨 황 스파이 4건. 0.16~0.18 구간도 전부 같은 사건이었고,
     0.15 이하는 같은 '사가'의 다른 각도(주가 급락 vs 이사회 조사)라 차단하면 과잉이었다.
   - 0.18 로 내리면 198건 중 7건(3.5%) 차단, 전부 실제 중복.
4. **발행 기록** — 게시 1건 = 파일 1개(`state/posts/<날짜>/<시각>-<키>.jsonl`).
   이전엔 모두 `state/posted.jsonl` 한 파일에 append 해서, 회차가 겹치면 rebase 충돌 →
   push 조용히 실패 → 기록 유실 → 다음 회차 재게시. 06:50 사고(13초 간격 이중 게시)가 이것이다.

과잉 차단은 감수한다(같은 사건 3연타보다 낫다). `dup:`·`dup_body:`·`digest:`·`stale_page:` 로그로 추적한다.

## 품질 게이트 — 라운드업·오피니언·종목추천 (`filters.is_digest`)

한 페이지에 여러 사건을 섞은 **라운드업**은 요약이 섞여 사고를 낸다.
2026-11-09 실측: Rio Times `The Big Read Brazil` 를 요약했더니 금리 동결 기사에 F1 브라질 우승이 붙었다.
같은 페이지가 06:50·07:30 두 번 게시되기도 했다(다음 날 같은 사실이 다른 URL로 다시 올라오는 구조).

| 종류 | 판정 | 예 |
|---|---|---|
| `digest` | 제목/URL에 daily brief·the big read·live blog·markets wrap·what to know 등 | Rio Times Big Read, Rio Daily Brief, MEE live blog, Bloomberg Markets Wrap |
| `opinion` | URL에 `/opinion/`·`/commentary/`, 제목 `, By 홍길동`·`Opinion:` | Premium Times "Any lessons from America's midterm elections?, By …" |
| `stockpick` | stock pick·stocks to buy·top picks·market outlook | "BUA Cement, Unilever, Jaiz Bank top stock pick this week" |

**발행일 최종 검증** — 본문 페이지의 `datePublished`(JSON-LD/meta/`<time>`)를 읽어 48시간 초과면 `stale_page` 로 폐기.
피드가 갱신·재게시 날짜를 주면 몇 주 전 글이 '방금'으로 들어오기 때문이다.
같은 이유로 `parse_feed` 는 `<published>` 를 `<updated>` 보다 우선한다(`updated` 는 최후 수단).

## 메시지 형태

```
<번역·요약된 2문장, 220자 이내, 해요체>

<blockquote expandable>출처
· 매체A
· 매체B</blockquote>
```
- 시각·분류·매체 메타 줄 없음. 링크 미리보기 끔.
- 60~80자 미만이거나 문장부호로 끝나지 않으면 **잘린 요약으로 보고 폐기**(`llm.is_complete`).
- Google News 링크는 실제 기사 URL로 해석해서 넘긴다(옛 링크를 넘기면 본문 추출이 100% 실패한다).

## 장애 대응

| 증상 | 볼 것 |
|---|---|
| 채널 침묵 | `gh run list` 최근 실행. 초록인데 "게시 없음"이면 후보가 필터에 걸린 것 |
| 실행이 아예 없음 | 예약 누락. `gh workflow run osint.yml` 로 블록을 띄운다 |
| 상태 커밋 실패 | Actions 권한이 Read-only 인지 확인 (Settings → Actions → Workflow permissions) |
| `llm: 실패` | 키 한도. 다른 키를 secret 에 추가하면 폴백 |
| 중복 게시 | 폰 데몬이 살아 있는지 (`pgrep -f osint.py`) |
| 60일 뒤 예약 정지 | 공개 저장소 무활동 시 자동 비활성. `keepalive.yml` 이 매주 커밋해 막는다 |

## 주간 루틴 (일요일, Asia/Seoul)

| 시각 | 태스크 | 산출물 |
|---|---|---|
| 01:00 | 주간 꿈꾸기 | `shared/dreams/weekly-YYYY-MM-DD.md` + memory 1항목 |
| 04:00 | 주간 자유시간 | `shared/journal/YYYY-MM-DD.md` + DM + 채팅 5줄 |
| 08:00 | OSINT 주간 점검 | DM 보고문 + 채팅 요약 |

DM: `python3 /var/minis/shared/osint/osint.py notify --file /tmp/report.txt`
(폰 `config.json` 의 `owner_chat` 사용. 공개 채널에는 뉴스만 보낸다.)

## 반응 피드백 루프 (2026-11-10)

Gram 이 채널 글에 다는 👍/👎 를 주간 점검에서 학습한다.

**어떻게 읽는가** — Bot API 에는 채널 글의 반응을 **조회하는 메서드가 없다**.
`message_reaction` 은 봇이 관리자인 그룹/채널로 오는 푸시일 뿐이고 지난 반응은
돌아오지 않는다. 대신 공개 채널이면 t.me 웹 미리보기(`t.me/s/<채널>`,
`?embed=1`)에 게시물별 반응 수가 실려 있다. 토큰도 사용자 계정도 필요 없다.
반응은 **개수만** 보인다(누가 눌렀는지는 비공개). 게시물 ↔ 게시 기록은
① 정규화 URL ② 시각 ±150초 최근접 순으로 잇는다. 새 게시분은 `send()` 가 남기는
`message_id`(`state` 행의 `m`)로 정확히 붙는다.

```
python3 feedback.py fetch --days 21    # t.me 긁어 state/feedback.jsonl 에 병합(반응은 최신값으로 교체)
python3 feedback.py report --days 21   # 👎 기사 + 기존 필터가 잡았는지 진단
python3 feedback.py apply --days 21    # 소스/비트 보정 → config.json feedback
```

**👎 를 셋으로 나눈다** — ① 기존 필터가 잡았어야 함(버그) ② 새 정크 패턴(PR·행사·
보도자료·깨진 링크) → 규칙 추가 ③ 취향(비트·지역·소재) → 점수 보정.
자동 반영은 ③만 한다. ①②는 사람이 판단해야 규칙이 쌓인다.

**보정식** — `adj = -2.5·👎 + 1.0·👍`(소스, 범위 −4~+2) /
`-1.5·👎 + 0.5·👍`(비트, −3~+1). 소스는 👎 1건부터, 비트는 2건부터 움직인다.
표본이 작으므로 상한을 두고, 창(기본 21일)을 벗어난 반응은 자동 소멸해
보정도 함께 되돌아간다. `osint.fb_adj()` 가 `pick` 정렬과 `_candidate_ok`
게이트 양쪽에 적용한다(게이트에만 걸면 1등 자리를 계속 차지한다).

**어떻게 쓰는가 (2026-11-10 표본 10건)** — 반응이 붙은 글만 '라벨'이고, 반응 없는
글은 중립(싫지도 좋지도 않음)이다. 표본이 10건대라 `apply` 로 소스를 통째로 흔들지 않고,
① 구조적 결함(깨진 링크·라이브블로그·티커)은 규칙으로 막고 ② 취향 신호는 `signals` 표에
쌓아 가설이 갈릴 때까지 기다린다. 표가 `feedback.py report` 끝에 자동으로 붙는다.

**첫 적용 사례 (#737)** — FormatBiz 의 행사 보도자료(Content Americas 2027). 게시 링크가 `…/dettNews.aspx?id` 로 잘려 있었다 → `junk_item` 에 '쿼리 값이 비어 끝나는 링크' 규칙 추가 + `tests/test_feedback_rules.py` 회귀 테스트. 같은 소스 점수는 −2.5.

**첫 표본 10건에서 나온 규칙 (2026-11-10)**
- `ROUNDUP_TITLE` 에 `live:` / `ROUNDUP_URL` 에 `/live/` 추가 — "Cop31 live:"(👎 #741).
  실측 영향 2/626건.
- 티커 정규식 `[A-Z]{2,3}` → `{2,6}` — `(NVDA:NASDAQ)` 가 빠져나갔다.
- 미결: 👎 4건 중 2건이 나이지리아(736 선거 관측, 743 정유공장 오더북)이고 둘 다
  '분석·전망' 프레이밍이다. 나이지리아 기사는 전체의 4.3%(27/626)인데 👎 4건 중 2건
  → 어느 쪽(지역 vs 프레이밍)인지 표본 4건으로는 못 가른다. 다음 표본에서 판정.

**한계** — 반응은 사후 신호다. 👎 가 없으면 아무것도 배우지 못하므로,
👎 3건 미만인 주는 `apply` 를 건너뛰고 '반응 부족'으로 보고한다.

## 이전 이력 (요약)

- 2026-11-06: 폰 `workspace/` → `shared/osint/` 이사, SQLite 상태.
- 2026-11-07: 단일 점수 랭킹 → 비트 분류·균형 배분, 중복 판정 강화, 무료 LLM 폴백.
- 2026-11-08: 지역 축 + 품질 필터, 폰 실행 폐기 → **GitHub Actions 이전**.
- 2026-11-09: URL 정규화·요약문 유사도·라운드업 게이트·발행일 검증, 상태 파일 분할.
- 2026-11-10: 채널 반응(👍/👎) 수집·점수 보정 루프(`feedback.py`), 상태 행에 `m`(message_id).

## 참고한 오픈소스 (같은 문제를 푼 프로젝트)

| 프로젝트 | 가져온 아이디어 |
|---|---|
| [Thysrael/Horizon](https://github.com/Thysrael/Horizon) | 후보 묶음을 **한 번의 LLM 호출**로 '같은 사건' 그룹핑 → 대표 1건 유지, 나머지는 출처만 병합. 우리 `dup_body` 의 상위 버전(비용은 회차당 1콜) |
| [Rongronggg9/RSS-to-Telegram-Bot](https://github.com/Rongronggg9/RSS-to-Telegram-Bot) | RSS→텔레그램 전달의 기본기: 구독별 필터, 미디어 처리, 실패 재시도 |
| [nkanaev/yarr](https://github.com/nkanaev/yarr) · [miniflux/v2](https://github.com/miniflux/v2) | 피드 리더의 상태 관리·키 정규화(`guid`/`link` 기반 중복) |
| [adbar/trafilatura](https://github.com/adbar/trafilatura) | 본문·메타데이터 추출 — 지금은 자체 추출기(`writer.py`). 페이월 실패가 잦아지면 교체 후보 1순위 |
| [fhamborg/news-please](https://github.com/fhamborg/news-please) | 뉴스 전용 크롤러·추출기 (발행일 파싱이 강하다) |

공통 교훈: **중복은 '글' 단위가 아니라 '사건' 단위로 판정해야 하고, 그 판정 재료는 본문·요약이어야 한다.**
URL·제목만 보는 필터는 매체가 늘어날 때마다 반드시 샌다.

