# WebPaper OSINT — 시스템 아키텍처

> 작성 2026-11-08. 이 문서 하나로 시스템 전체를 이해하고 다른 사람(또는 다른 AI)과
> 논의할 수 있도록 썼다. 코드는 `github.com/yousungcake-wq/webpaper-osint`.

---

## 0. 한 문장 요약

**96개 RSS/Google News 피드를 5분마다 수집해, 점수화·분류·균형 배분을 거쳐 기사 1건을
고르고, LLM으로 2문장 한국어 요약을 만들어 텔레그램 채널(@webpaperHQ)에 게시하는
파이프라인.** 실행은 GitHub Actions 러너에서, 트리거는 cron-job.org 에서 온다.
같은 회차가 `data/feed.json` 을 갱신하고 정적 사이트(<https://yousungcake-wq.github.io/webpaper-osint/>)가
그 파일을 읽어 웹 피드로도 보여준다 — 출력이 둘(텔레그램·웹)이고 입력·선택 로직은 하나다.
사이트 쪽 결정과 한도는 [`SITE.md`](SITE.md).

---

## 1. 구성 요소와 책임 경계

| 구성 요소 | 책임 | 하지 않는 것 |
|---|---|---|
| **cron-job.org** | 5분마다 GitHub API `workflow_dispatch` 호출 (트리거만) | 어떤 데이터도 보지 않음 |
| **GitHub Actions** | 실행 환경. 수집·채점·선택·요약·전송·상태 커밋 | 스스로 시간을 세지 않음(트리거가 없으면 안 돎) |
| **텔레그램** | 결과 출력 (채널 게시) | 입력 없음 |
| **GitHub Pages + `site/index.html`** | 결과 출력 (웹 피드, `data/feed.json` 을 읽음) | 스스로 데이터를 만들지 않음 |
| **RSS / Google News** | 입력 | — |
| **LLM API** (Gemini·Groq·OpenRouter) | 요약 + 비트 분류 | 사실 검증은 하지 않음 |
| **폰 nanoMuse 샌드박스** | 주간 점검·자유시간·꿈꾸기 보고서 작성 | **보도 파이프라인에는 관여하지 않음** |

폰과 GitHub의 의존은 단방향이다. 폰이 꺼져도 보도는 계속되고, 폰은 주 3회 결과를 읽어
사람에게 보고할 뿐이다.

---

## 2. 실행 모델

### 2.1 트리거 체인

```
cron-job.org (5분)  ──POST──▶  GitHub API
                                /repos/{owner}/{repo}/actions/workflows/osint.yml/dispatches
                                body {"ref":"main"}
                                     │
                                     ▼
                        workflow: osint.yml (job: cycle)
                          - checkout
                          - setup-python 3.12
                          - python run_once.py
                          - (상태 커밋은 스크립트가 직접 수행)
```

세 겹의 트리거를 두고 있으나 실제 주 트리거는 외부 크론이다.

| 트리거 | 주기 | 현재 상태 |
|---|---|---|
| cron-job.org → `workflow_dispatch` | **5분** | 주 트리거. 실측 17:00/17:05/17:10/17:15/17:20 정확 |
| GitHub `schedule` (`*/5`) | 명목 5분 | **실측 30~39분 간격** — 이 저장소/계정에서 스로틀됨 |
| `osint-block.yml` (수동) | 비상 | 예약이 며칠 죽었을 때 5시간 30분 자율 실행. **잡 타임아웃 355분** |

> GitHub 공식 문서상 `schedule` 은 최소 5분 간격이며, 정각 부하 시 지연·드롭될 수 있다.
> 이 계정에서는 신규 저장소 등록 직후 **약 90분간 0회 발화**했고, 이후에도 5분을 지키지
> 않았다. 깨끗한 테스트 저장소로 재현해 저장소 문제가 아님을 확인했다.

### 2.2 워크플로 파라미터

워크플로는 **두 개로 분리**되어 있다. 한 파일에 `minutes` 입력과 `timeout-minutes: 15` 를
같이 두면 "5시간 돌려라 / 15분에 죽인다"가 충돌한다(2026-11-08 실제 모순, 아래 7장 참고).

```yaml
# osint.yml — 5분 주기 단발
timeout-minutes: 15
LOOP_MINUTES: "0"

# osint-block.yml — 수동 비상용
timeout-minutes: 355          # 330분 블록 + 여유, GitHub 잡 상한 360분
LOOP_MINUTES: "330"
```

공통: `concurrency: { group: osint-cycle, cancel-in-progress: true }`,
`permissions: { contents: write }`, `COMMIT_STATE: "1"`, `DRY_RUN`(수동 미리보기).

- 블록은 일반 회차와 **같은 concurrency 그룹**이다. 크론이 살아 있으면 5분 뒤 다음
  회차가 블록을 취소한다 = "크론이 죽었을 때만 의미 있는 비상구".
- `LOOP_MINUTES=N` 이면 5분 경계에 맞춰 N분 동안 반복 실행.
- `cancel-in-progress: false` — 회차는 **순서대로** 실행된다. 취소 방식이면 두 회차가
  같은 상태를 읽고 같은 기사를 동시에 고를 수 있다(2026-11-09 중복 사고).
- `cancel-in-progress: true` — 회차가 길어져 다음 트리거와 겹치면 이전 회차를 버리고 새로 시작.
- 입력값은 숫자 검증 + 상한 340분으로 클램프(`run_once.main`).

### 2.3 한 회차의 흐름 (`run_once.cycle()`)

```
make_con()            in-memory SQLite 생성
load_state(con)       state/posted.jsonl → items 테이블 (최근 5일)
shard_sources()       이번 샤드의 피드만 남기도록 news_fetch.load_sources 를 감쌈
osint.collect()       96/2 = 48 피드 병렬 수집 → 채점 → items 삽입
beats.classify_pending()  키워드 1차 + LLM 배치 2차 분류
osint.llm_publish()   cap 확인 → 후보 선택(pick) → 본문 추출 → 요약 → 전송
append_state()        게시 성공 시 상태 1줄 추가
commit_state()        git add/commit/pull --rebase/push
```

---

## 3. 데이터 모델

### 3.1 왜 SQLite 파일을 커밋하지 않는가

액션 러너는 매 실행마다 **새 컨테이너**다. 상태를 유지하려면 저장소에 커밋해야 하는데,
SQLite 파일(6.9MB)을 5분마다 커밋하면 히스토리가 하루 수 GB로 불어난다.

→ **append-only JSONL** 로 바꿨다. 게시 1건 = 1줄 = 약 220B.
현재 파일 크기 69KB / 314줄 / 5일 보관. 커밋 diff는 한 줄이라 저장소 증가는 하루 수십 KB.

### 3.2 상태 스키마 (`state/posted.jsonl`)

```json
{"k":"<sha1(norm_title)[:16]>","t":"제목","f":"피드명","c":"카테고리",
 "b":"TECH","r":"EUROPE","s":18.4,"a":"2026-11-08 15:16 UTC"}
```

| 필드 | 의미 | 쓰이는 곳 |
|---|---|---|
| `k` | 제목 정규화 해시 | 기사 중복(같은 글) 판정 |
| `t` | 제목 | 사건 중복 판정, df 계산, 최근 80건 비교 |
| `f` | 피드명 | `feed_health` 사후 보정 |
| `b` | 비트 | 비트별 평균·표준편차(`beat_stats`), 쿼터(`quota_penalty`) |
| `r` | 지역 | 지역 균형(`region_deficits`) |
| `s` | 점수 | z점수 계산 |
| `a` | 게시 시각 | 시간당 상한(최근 1시간), 정렬 |

### 3.3 in-memory SQLite 재수화

매 회차 `items / tr / meta / gnurl / runs` 테이블을 메모리에 만들고 상태를 넣는다.
이유: `beats.py`·`osint.py` 의 선택 로직이 전부 SQL 질의로 짜여 있어서, **엔진 코드를
한 줄도 고치지 않고** 상태만 갈아끼울 수 있다. 로직 재작성은 곧 품질 회귀다.

부작용: `gnurl`(구글뉴스 URL 해석 캐시)과 `runs`(수집 통계)는 매 회차 사라진다.
전자는 회차당 몇 건뿐이라 손해가 작고, 후자는 `audit()` 이 소스 생존 확인으로 대체한다.

---

## 4. 파이프라인 상세

### 4.1 수집 (`osint.collect`)

- 입력: `sources.txt` — `분류|이름|URL` 형식 **96개** (일반 RSS + Google News 쿼리).
- **샤딩**: `idx = int(time.time()//300) % 2` → 회차마다 절반(48개)만 수집. 피드당 10분 주기.
  96×288 = 하루 27,648요청을 절반으로 줄이기 위한 것.
- 병렬: `ThreadPoolExecutor(max_workers=12)`, 피드당 `urlopen(timeout=12)`.
- **전체 예산 45초**: `wait(futs, timeout=45)` 후 남은 future는 `Timeout: 사이클 예산 초과`로 기록.
- 기사 만료: `ts < now - 8h` 는 버린다(보도 가능 창은 그보다 짧은 6시간).
- 제목 정규화 후 `key = sha1(norm_title)[:16]`. 같은 key가 이미 있으면 점수만 갱신.
- Google News 제목은 `" - 매체명"` 접미사를 정규식으로 제거.

### 4.2 채점 (`osint.score_item`) — 0~35점 스케일

| 축 | 예시 | 가중 |
|---|---|---|
| 카테고리 | conflict 5.0 > politics/economy/finance/tech 4.0 > google 1.5 | 기준값 |
| 전쟁·군사 | airstrike·nuclear site·escalat·retaliat | +3 |
| 저보도 지역 | 수단·다르푸르·RSF·사헬·M23·티그라이 | +3~4 |
| 중동 | 예멘·후티·가자·헤즈볼라·이란 | +3 |
| 인도적 | famine·massacre·genocide·coup·junta | +3 |
| 최근성 | 4시간 창 | 최대 +4 |
| 스포츠·연예 | world cup·k-pop·box office | −8 |
| 오피니언·리스트 | "5 Ways…", "What to know" | −6 |
| 로컬 증시·현지통화 | naira·peso·sensex | −5 |
| 시세 나열 | "Gold Rates Today", "stocks to buy" | −9 |
| **저신호 외교** | condol·mourn·congratulat·birthday | **점수 6.0 상한(사실상 탈락)** |

**지역 채점**(2026-11-08 추가): 같은 LLM 콜에서 지역 라벨까지 받는다(추가 호출 없음).
`kw_region` 키워드 판정은 폴백으로만 남는다. 60건 표본 실측 — 키워드만 쓸 때 `GLOBAL` 37%,
LLM 라벨 적용 후 **15%**.

`COURTESY` 상한은 2026-11-08 추가. "정상이 부친상 조의" 기사가 `niger +3 / junta +3 /
coup +3` 중첩으로 **33점 1위**를 차지한 사례가 있었다.

### 4.3 분류 (`beats.classify_pending`)

1. **1차(키워드)** — `kw_beat(title, cat)`. 짧은 단어는 단어경계 정규식으로 매칭
   (`ai` 가 `said` 에 걸리는 버그 방지), `West Bank → FINANCE` 같은 오분류는 `OVERRIDES` 로 교정.
2. **2차(LLM 배치)** — 상위 60건을 **한 콜**로 비트 라벨링(Groq `openai/gpt-oss-120b`,
   실패 시 Gemini). `beat_src='llm'|'kw'` 로 출처 구분. 타임아웃 20초.
3. `OTHER`(연예·스포츠·일반)는 게시 대상에서 제외.

비트 5종: `TECH ECONOMY CONFLICT POLITICS (+OTHER=기타, 게시 제외)`.
2026-11-10 통합: 금융·원자재를 ECONOMY 로 합쳤다(사용자 요청). 예전 라벨은 `beats.norm_beat()` 가 흡수한다.

### 4.3b 형식 게이트 (`beats.drop_form`, 2026-11-12)

**왜 생겼나**: 👎 11건의 사유를 세어 보니 소재·지역이 아니라 **글의 형식**이었다
(일상 시황·보도자료·상황보고·전망-only·지방 정치). 비트는 "무엇에 관한가"만 묻기 때문에
이것들이 ECONOMY/OTHER 로 통과했다. 실측: 최근 27시간 게시 212건 중 35~40%가 이 유형.

**계약**: 4.3 의 분류 콜 출력이 `번호:비트:지역:형식:유용도` 로 넓어졌다(추가 호출 0).
- 형식: `NEWS MARKET PROMO FORECAST LOCAL REPORT LIFE`
- 유용도: 0–10 (9-10 체계적 / 7-8 중요 / 5-6 참고 / 3-4 일상 등락·예상된 결과 / 0-2 홍보·소문)
- 게이트: `형식 != NEWS && 유용도 < 4` → 차단(`note='form:<형식>'`). `NEWS` 도 유용도 1 이하면 버린다.
- 유용도를 못 받으면(구형 3필드 응답·LLM 실패) **판단하지 않는다** — 애매하면 남긴다.

**토큰 예산 (중요)**: Groq 무료 한도는 200k 토큰/일인데, 회차마다 60건을 다시 분류하면
하루 3~4배를 넘겨 429 로 떨어지고 게이트가 통째로 꺼진다. 그래서 두 장치를 뒀다.
1. `state/forms/<날짜>/<시각>.jsonl` — 기사 key → 판정 캐시. 한 기사는 **한 번만** 분류한다.
   (회차마다 새 파일. 하나의 JSONL 을 고쳐 쓰면 커밋마다 전체 blob 이 새로 생긴다.)
2. 비트·지역 배치는 `BULK_EVERY_MIN`(기본 60분)에 1회. 그 사이에 뽑힌 후보는
   `beats.classify_one()` 이 **단건**으로 분류한다(~130토큰, 회차당 1건까지).

**LLM 이 죽었을 때**: `filters.market_wrap()` 이 시황만 잡는다(실측 정밀도 17/17, 재현율 63%).
평상시 경로가 아니라 백스톱이다.


### 4.4 선택 (`osint.pick` + `run_once.install_loose_pick`)

**2단 레인** (원 설계, `osint.pick`):

1. **우선 레인** — 자기 비트 안에서 `z ≥ 4.0` 이면 쿼터 무시. 단 같은 비트가 최근 5건 중
   3건 이상이면 차단(폭주 방지).
2. **균형 레인** — 목표비에서 모자란 비트부터. 단 `z ≥ 0.8`(자기 비트 평균 이상)만.
   목표비: `TECH2 ECONOMY2 CONFLICT2 POLITICS1 OTHER1` (최근 10건 기준.
   2026-11-12: ECONOMY 3→2, OTHER 2→1 — 오염률이 ECONOMY 60%·OTHER 45%(CONFLICT 6%)라
   쿼터가 오염 배분기가 됐다. 이 값은 '채울 순서'만 정하므로 낮춰도 분포는 유지된다),
   지역: `EUROPE3 LATAM2 US2 ASIA2 AFRICA2 MIDDLE_EAST1` (최근 12건 기준).

**z점수**: `(score − 비트평균) / 비트표준편차`. 원점수는 비트마다 분포가 달라
(분쟁 20~30, 테크 15 상한) 직접 비교가 불공정하기 때문에 정규화한다. 표본 20건 미만인
비트는 기본값 `(9.0, 3.5)` 를 쓴다.

**문제**: 5분 주기로 돌면 목표비가 이미 채워져 있어 2단 레인이 빈 회차가 잦다 → 채널이 빈다.
그래서 안전망 두 단계를 덧붙였다(`run_once`):

3. **3차** — 최근 게시가 가장 적은 비트부터, 그 비트의 상위 후보(품질 하한 통과분).
4. **4차** — 점수 상위. 단 `min_score + 3` 이상, 그리고 `PRICE_LIST`·`LOW_VALUE`·
   `COURTESY`·`NOISE_TITLE` 을 통과해야 한다(바닥 훑기 방지).
   2026-11-12 '빈 슬롯 허용': 안전망은 형식이 NEWS 가 아니면 **유용도 5 이상**만 통과시킨다
   (본선은 4). 좋은 후보가 없으면 그 회차는 침묵한다 — 물량이 시간당 7.9건으로 상한(10)에
   여유가 있었다.

**사건 예산** (`osint.story_penalty`): 최근 24시간 게시 제목에서 같은 고유명사가 3건 이상
나왔으면 그 이야기는 균형 레인·안전망에서 **−6점**으로 밀린다(차단 아님 — 우선 레인은 그대로).
실측: 시황·전망을 걷어낸 뒤에도 젠슨 황 스캔들이 24시간에 7건 나갔다.

**게시 상한**: `cap_hit()` — 최근 1시간 게시 수 ≥ `max_posts_per_hour`(10)이면 건너뛴다.
이 상한은 LLM 경로에도 적용된다(2026-11-08 이전에는 기계작성 경로에만 있어 실측 13건/시간).

**품질 하한**: `min_score = 11.5`.

### 4.5 중복 판정 (`same_event`)

두 단계다.

1. **같은 글** — `key = sha1(norm_title)` 가 이미 items 에 있으면 갱신만.
2. **같은 URL** — 상태의 `u` 필드(기사 URL)로 차단한다. 제목이 살짝 바뀌면 `key` 가
   달라져 같은 기사가 몇 시간 뒤 다시 올라온다(2026-11-09: 4시간 20분 간격 재게시 실측).
3. **같은 사건(다른 매체)** — `same_event(a, b, df)`:
   - `toks()` 로 토큰화 + `_stem()` 어간 축약(ing/ed/ies/ly/s 제거) →
     kill/killed, strike/strikes, Houthi/Houthis 를 같은 토큰으로.
   - 판정식(OR): 자카드 > 0.45 **또는** 공통토큰 ≥ 4 **또는**
     공통토큰 ≥ 3 & (사상자 어휘 있음 | 자카드 ≥ 0.30) **또는** 희귀토큰(df ≤ 전체 2%) 2개 이상.
   - `df` = 최근 7일 제목의 토큰 문서빈도(`df_map`, 10분 TTL 캐시). 희귀 토큰 = 개체명 대리.
   - 차단되면 `note='dup'`, 로그에 `dup: A ≒ B` 를 남겨 과잉 차단을 추적.

과잉 차단(같은 전장의 다른 사건을 묶는 것)은 **의도적으로 감수**한다.
사나 공습이 1시간 40분 사이 3번 게시된 사고가 계기였다.

### 4.6 본문 추출 (`writer.py` / `osint.make_draft`)

- Google News 링크는 `writer.resolve_google()` 로 실제 기사 URL 해석
  (해석된 URL을 **반환 튜플에 반영**해야 한다 — 안 하면 본문 추출이 100% 실패한다).
- `fetch_html` → JSON-LD 본문 → 문단 추출 → 문장 점수화(`score_sent`) → 2문장 조립.
- 본문 300자 미만이면 폴백:
  - RSS `desc` 150자 이상 → 그대로 사용(`body fallback(descr)`)
  - `desc` 70자 이상 & 점수 ≥ 14 & 리스트형 아님 → **thin 모드**(2문장 속보, 180자 제한)
  - 그 외 → `note='no_body'` 로 버리고 다음 후보

### 4.7 요약 (`llm.summarize`)

- 프롬프트: "2문장 한국어, 220자 이내, 직함은 원문 그대로, 없는 수치·직함 생성 금지".
  thin 모드는 "스니펫에 있는 사실만, 180자 이내".
- 폴백 순서: **Gemini**(`gemini-3-flash-preview` → `gemini-flash-latest` →
  `gemini-3.1-flash-lite`) → Cerebras → Mistral → Groq(`gpt-oss-120b` →
  `qwen3.8-27b` → `gpt-oss-20b`) → OpenRouter. 키가 있는 것만 시도.
- **시간 예산 60초**, 재시도 1회 제거, 제공자당 `timeout=30`.
  (2026-11-08: 2회 재시도 × 3제공자 × 90초 타임아웃이 겹쳐 회차가 5분을 넘겼다.)
- **잘린 응답 차단** — `is_complete()`: 80자 미만(thin 60자) 또는 마지막 글자가
  문장부호가 아니면 폐기. 실제 사고(#36 "…그는 민") 후 추가.
- 429·한도 오류가 난 제공자는 30분 쿨다운(`_COOLDOWN`).
- Python 기본 User-Agent 는 Groq 등에서 Cloudflare 1010 → 403. 브라우저 UA 필수.

### 4.8 전송 (`osint.send`)

```
<요약 2문장>

<blockquote expandable><b>출처</b>
· 매체A
· 매체B</blockquote>
```

- HTML `parse_mode`, `disable_web_page_preview=true`, `timeout=30`.
- 채널에는 뉴스만. 운영 보고는 개인 DM(`osint.notify`).

### 4.9 상태 커밋 순서

전송 성공 **직후** `append_state()` → `commit_state()`. 취소로 인한 중복 게시 위험을
최소화하기 위한 순서다(창은 1~2초). `commit_state` 는 `git add → commit → pull --rebase
--autostash → push` 를 subprocess 로 수행하고, 실패해도 회차를 죽이지 않는다.

---

## 5. 관측성

| 신호 | 위치 |
|---|---|
| 회차 로그 | Actions 실행 로그. `[HH:MM] collect +N err=M (샤드 i/2)`, `pick(3차·FINANCE 최근1건)`, `posted: …`, `이번 회차 게시 없음` |
| 상태 커밋 | `state: YYYY-MM-DD HH:MM UTC` — **게시가 곧 하트비트** |
| 소스 생존 | `audit(con)` — 정상/정체/실패/일시오류 + 상위 출처 + 일별 보도 |
| 실행 건강 | `gh api .../actions/runs` — 성공률, 실행 간격(예약 공백 탐지) |
| 주간 보고 | 일요일 08:00 태스크가 위 셋을 합쳐 개인 DM |

침묵의 원인은 대개 셋 중 하나다: ① 트리거 없음(이력 0건) ② 상한 도달(최근 1시간 10건)
③ 후보 전멸(필터 과잉). 로그의 마지막 줄이 어느 쪽인지 알려준다.

---

## 6. 구성 표면

### 6.1 `config.json`

| 키 | 값 | 의미 |
|---|---|---|
| `channel` | `@webpaperHQ` | 게시 채널 |
| `owner_chat` | (저장소에서는 비움) | 주간 DM 수신자 |
| `min_score` | 11.5 | 보도 최소 점수 |
| `max_age_hours` | 6 | 이 시간 지난 기사 폐기 |
| `max_posts_per_hour` | 10 | 시간당 상한 |
| `mode` | `llm` | 요약 경로 |

### 6.2 시크릿 (GitHub Actions Secrets)

`TELEGRAM_BOT_TOKEN` · `GEMINI_API_KEY` · `GROQ_API_KEY` (+ 선택: `MISTRAL_API_KEY`
`CEREBRAS_API_KEY` `OPENROUTER_API_KEY`)

### 6.3 소스 (`sources.txt`)

`분류|이름|URL` 96줄. Google News 쿼리는 `GN <주제>` 이름으로 구분하며, 원 매체명을
복원해 표기한다.

---

## 7. 장애 카탈로그 (실제 발생분)

| 날짜 | 증상 | 원인 | 수정 |
|---|---|---|---|
| 11-06 | 게시 60분 침묵 | 구글뉴스 해석 URL을 DB에만 쓰고 반환은 옛 링크 → 본문 추출 100% 실패 | `_candidate_ok` 가 해석된 URL 포함 튜플 반환 |
| 11-07 | 잘린 요약 게시(#36) | 길이 하한 60자만 검사 | `is_complete()` — 80자·문장부호 검사 |
| 11-07 | 사나 공습 3연타 | 형태소 미결합 + 자카드 0.45 단일 임계 | `_stem` + 4중 OR 판정 |
| 11-08 | 앱 종료 후 데몬 미복구 | 리팩터 잔재 `errors_skipped` NameError 로 매 루프 즉사 | 참조 제거 |
| 11-08 | 회차 9~20분 | 피드별 12초×2재시도, 전체 예산 없음 | 45초 전체 예산 + `cancel_futures` |
| 11-08 | 수동 실행이 매번 5시간 블록 | 워크플로 입력 `minutes` **기본값 330** | 기본 0 |
| 11-08 | 회차가 5분 초과 | LLM 2회 재시도 × 3제공자 × 90초 | 60초 예산·재시도 제거·30초 타임아웃 |
| 11-08 | 예약이 90분간 0회 | 신규 저장소/계정 스로틀(테스트 저장소로 재현) | 외부 크론 도입 |
| 11-08 | 투자 분석 기사 게시 | 후보 없을 때 점수 상위만 훑음 | 정크 도메인·티커 패턴 + 3·4차 레인 품질 하한 |
| 11-09 | 채널 중복 23건 | ① 폰 데몬 부활(같은 채널 병렬 게시) ② 같은 URL 재게시 ③ 동시 실행 경쟁 | ① STOP 센티널+가드 ② URL 기반 차단 ③ 순차 실행 |
| 11-08 | **비상구가 15분에 잘림** | `minutes=330` 과 `timeout-minutes: 15` 가 한 파일에 공존 | 워크플로 분리. 블록으로 **34분 생존 실측 확인** |

---

## 8. 보안 태세

- 저장소 **공개**. 비밀은 Actions Secrets 에만. git 히스토리·추적 파일 스캔 결과 0건.
- 트리거가 `schedule` + `workflow_dispatch` 뿐 → **포크 PR 로 임의 코드 실행 경로 없음**.
- 크론용 PAT: fine-grained, 저장소 1개, `Actions: write` 만. 유출 시 가능한 것은
  워크플로 실행/취소 + 선언된 입력 전달까지. 입력은 셸 보간되지 않고 env 로만 전달.
- 워크플로 기본 토큰 권한 **read**, 각 워크플로가 `contents: write` 만 선언.
- 허용 액션은 **GitHub 소유만**, 버전은 커밋 SHA 고정 + Dependabot 주간 갱신.
- Secret scanning + push protection 켜짐.

---

## 9. 비용과 제약

| 항목 | 현재 |
|---|---|
| 고정비 | 0원 (공개 저장소 + 무료 크론 + 무료 LLM 티어) |
| 러너 사용 | 회차당 20~50초 × 288회/일 ≈ 하루 2~4시간 |
| LLM | 무료 티어. 게시할 때만 호출(시간당 최대 10건) |
| 주간 보고 | 폰 스케줄 세션 3회/주 |

**회색지대**: GitHub 는 Actions 를 "프로젝트와 무관한 연산"에 쓰는 것을 금지한다.
이 프로젝트는 그 저장소 자체가 곧 프로젝트이므로 성격상 다르지만, 러너를 하루 종일
점유하는 블록 모드를 상시로 쓰면 해석 여지가 있다. 문제가 되면 월 $4~6 VPS 로 옮기는
것이 정답이다(코드 수정 거의 없음).

---

## 10. 알려진 약점 (정량)

최근 5일 게시 314건 기준.

| 문제 | 수치 | 원인 |
|---|---|---|
| **지역 분류 공백** | `GLOBAL` 136건(43%) — *과거 데이터*. 2026-11-08부터 LLM 지역 라벨 도입(표본 37%→15%) |
| **남미 부재** | `LATAM` 2건(0.6%) | 남미 소스 자체가 적고 Google News 쿼리 의존 |
| **지역 정치 기사 혼입** | 나이지리아·필리핀 사례 반복 | 규모(국제적 파급) 판정 축이 없음 |
| **음차 오류** | "지가와 주 더체"(Jigawa, Dutse) | 번역·해요체 변환이 고유명사를 그대로 음차 |
| **비트 편향** | TECH 62 · FINANCE 47 이 상위 | 소스 구성(테크 매체 다수) + 3차 레인의 최근건수 기준 |

---

## 11. 용어

- **비트(beat)** — 뉴스 분야 단위(TECH/FINANCE/CONFLICT/COMMODITY/ECONOMY/POLITICS).
- **z점수** — `(값 − 평균) / 표준편차`. 분포가 다른 집단끼리 비교하기 위한 정규화.
- **자카드 유사도** — `|A∩B| / |A∪B|`. 두 제목의 토큰 집합 유사도.
- **df(document frequency)** — 어떤 토큰이 등장한 문서 수. 낮을수록 희귀 = 개체명일 확률이 높다.
- **샤드(shard)** — 수집 부하를 나누기 위해 소스 목록을 등분한 묶음.
- **멱등(idempotent)** — 같은 회차가 두 번 돌아도 결과가 중복되지 않는 성질. 여기서는 `key` 중복 +
  `posted` 상태 + 시간당 상한의 조합으로 달성.
- **concurrency group** — 같은 그룹의 워크플로가 동시에 돌지 않게 하는 GitHub 기능.
- **fine-grained PAT** — 저장소·권한을 지정해 발급하는 GitHub 토큰.
- **in-memory SQLite 재수화** — 파일 대신 메모리에 DB를 만들고 상태를 넣어 기존 SQL 로직을 재사용하는 기법.

---

## 12. 다른 AI와 논의해볼 질문

1. 지역 분류를 LLM 으로 보정할 때, 5분 주기·60초 예산 안에서 비용 없이 가능한 설계는?
   (배치 분류 프롬프트에 지역 라벨을 합치는 방식의 정확도 한계는?)
2. 시간당 10건 상한과 5분 주기는 사실상 같은 제약이다. 큐레이션 품질을 올리려면
   주기를 늘리고 상한을 낮추는 게 맞나, 반대인가?
3. `same_event` 의 4중 OR 규칙은 재현율을 올리는 대신 정밀도를 희생한다.
   이 트레이드오프를 측정할 최소 비용의 지표는 무엇인가?
4. 상태를 append-only JSONL 로 두는 설계의 확장 한계는? (게시 이력이 수만 건이 되면
   in-memory 재수화 비용과 df 계산이 병목이 될 것)
5. GitHub Actions 를 실행 호스트로 쓰는 것의 정책 리스크를 줄이면서 0원을 유지하는
   대안은? (Cloudflare Workers cron 은 무료 CPU 10ms 로는 96피드 파싱이 불가)
