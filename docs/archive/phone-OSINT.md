# OSINT 운영 문서 (Leyley → Gram)

> **복구**: 이 문서 → `/var/minis/shared/dreams/YYYY-MM-DD.md` 순서로 읽는다.
> 코드 백업: `/var/minis/shared/osint/` (osint.py·llm.py·writer.py·config·sources·DB 스냅샷)

24시간 돌아가는 국제 뉴스 OSINT. **수집은 1시간 주기, 보도는 5분 주기 최대 1건(시간당 10건 상한)**, 매주 일요일 아침 점검 보고.

## 구조 — **모든 파일은 `/var/minis/shared/osint/` (2026-11-06 이사 완료)**
`/var/minis/workspace/` 는 세션마다 새로 만들어지므로 쓰지 않는다. 코드·DB·큐·로그 전부 shared.

```
/var/minis/shared/osint/
  osint.py        엔진 (collect / tick / daemon / status / audit / draft / send)
  news_fetch.py   RSS·Atom·Google News 수집기 (load_sources/fetch/parse_feed/norm_title)
  llm.py          무료 LLM 요약 (Gemini → Groq → OpenRouter)
  writer.py       기사 본문 추출·번역·해요체 변환
  dedupe.py       DB 제목 중복 정리 + key 재계산 (수동 실행)
  beats.py        비트 분류(TECH/FINANCE/CONFLICT/COMMODITY/ECONOMY/POLITICS) + 균형 배분
  keepalive.sh    데몬 생존 확인·재시작 (매시 07분 태스크가 호출)
  config.json     채널·점수 임계값·주기
  sources.txt     87개 소스 (분류|이름|URL)
  osint.db        SQLite: items · tr · gnurl · runs · meta
  queue/          모델 작성 대기 초안
  daemon.log      로그 (200KB 로테이션)
  daemon.pid      PID
```
소스 목록은 `/var/minis/shared/osint/sources.txt` (87개, `분류|이름|URL`).

## 운영 모드
- `config.json` 의 `mode` = **"llm"** (기본) | "model" | "auto"
  - **llm**: 데몬이 5분마다 후보 1건을 골라 무료 LLM API 폴백(Gemini→Cerebras→Mistral→Groq→OpenRouter)으로 2문장(220자 이내) 해요체 요약을 만들고 바로 게시한다. **시간당 상한은 llm 경로에도 적용된다**(`cap_hit()`, 2026-11-08 수정). 채팅 세션 생성 없음, 비용 최소.
    - 키: `GEMINI_API_KEY` / `GROQ_API_KEY` / `OPENROUTER_API_KEY` (환경변수)
    - 실패 시 초안이 큐에 남고, 시간당 `OSINT 보도 예비` 태스크가 모델로 마저 쓴다.
  - **model**: 데몬은 1시간 주기 수집만 한다. 보도는 10분마다 도는 스케줄 태스크가 `osint.py draft` 로 후보+기사 본문을 큐에 만들고, 모델이 한국어 3~4문장으로 정리해 `osint.py send` 로 게시한다. 품질 우선.
  - **auto**: 모델 없이 기계 번역 기반으로 데몬이 직접 게시(무료·빠름·문장 품질 낮음).
- 큐: `/var/minis/shared/osint/queue/<id>.json` (3시간 지나면 폐기)

## 동작
1. **collect** — 46개 RSS/Google News 병렬 수집(약 5초). 제목 정규화 해시로 중복 제거, 키워드·출처·최근성으로 0~35점 채점.
2. **pick→publish** (5분) — 미보도 기사 중 비트 균형·z점수 기준으로 1건 선택. `min_score`(11.5) 미만이면 침묵. 6시간 지난 기사는 폐기, 이미 보도한 사건과 제목 유사도 0.45 이상이면 중복 처리. **1건 = 1메시지**로 채널 전송.
3. **번역** — Google 번역 엔드포인트(무료, 키 불필요)로 제목·요약을 한국어로. 캐시 테이블에 저장해 재번역 안 함.
4. **daemon** — 30초마다 깨어나 하트비트 기록, 수집/보도 시각이 되면 실행.

## 채점 (관심사 중심, 2026-11-07 재조정)
Gram 관심사: **국제분쟁 — 특히 아프리카·중동의 저보도 분쟁.** 전쟁·분쟁 기사는 감점하지 않는다.
- 카테고리 가중: **conflict 5.0** > politics/economy/finance/tech 4.0 > google 1.5
- 전쟁 키워드: 그대로 가산(과거 0.5배 감점은 폐지). `airstrike`·`nuclear site`·`escalat`·`retaliat` +3, `war`·`bomb`·`blockade`·`mobiliz` +2
- 지역 가중(THEATER_WORDS): 수단·다르푸르·RSF·사헬·M23·고마·티그라이 +3~4, 예멘·후티·가자·헤즈볼라 +3, 이란 +3
- 인도적 가중(HUMAN_WORDS): famine·massacre·genocide·coup·junta·militia·war crime +3
- 노이즈 감점: 스포츠·연예(월드컵·프리미어리그·K-pop·박스오피스 등) −8
- 최근성 4시간 창 최대 +4. 후보가 없으면 침묵, 점수 높은 순서로 5분마다 1건.

실측(2026-11-07):
```
25.0  Darfur famine / displacement camps     (Radio Dabanga)
20.0  RSF strikes Ed Damazin                 (GN SudanTrib)
17.0  Iran iodine tablets vs US strikes      (Economic Times)
 7.0  Spain World Cup co-host controversy    (탈락)
```

## 배분 방식 — 2단 레인 (2026-11-07 개정)
쿼터가 중요도를 누르는 문제(24점 가자 사건이 만료)가 확인되어 구조를 바꿨다.

1. **우선 레인** — 자기 비트 안에서 z ≥ 4.0(이례적)이면 쿼터를 무시하고 즉시 게시.
   단 같은 비트가 최근 5건 중 3건 이상이면 막는다(폭주 방지). 25점 분쟁이 12점 채우기 기사에 밀리지 않는다.
2. **균형 레인** — 나머지는 목표비 부족분 순서로 채운다. 단 **그 비트 평균 이상(z ≥ 0.8)** 인 기사만 후보가 된다.
3. **z점수 정규화** — 원점수는 비트마다 기준이 다르다(분쟁 20~30, 테크 15 상한). 그래서 `(score-평균)/표준편차` 로
   자기 비트 안에서 얼마나 튀는지를 잰다. 실측: CONFLICT 평균 9.5·sd 4.8, TECH 평균 9.2·sd 2.5.

## 비트 균형 배분 (2026-11-07, 알고리즘 교체)
점수 1등만 뽑는 방식은 한 분야(분쟁)가 채널을 독점한다 → **분류 후 배분**으로 바꿨다.
1. **1차 분류(키워드)** — 수집 직후 모든 기사에 비트를 붙인다(`beats.kw_beat`). 짧은 단어는 단어경계로 매칭(`ai`가 `said`에 걸리는 것 방지), `West Bank` 같은 함정은 OVERRIDES 로 교정.
2. **2차 분류(LLM)** — 매시 수집 직후 상위 30~40건을 **한 콜에 배치 분류**(Groq gpt-oss-120b, 2초). 실패하면 키워드 라벨 유지. `beat_src` 로 구분.
3. **배분** — 최근 10건의 비트 분포를 보고 목표비 `TECH2·FINANCE2·CONFLICT2·ECONOMY2·COMMODITY1·POLITICS1` 에서 가장 모자란 비트부터 채운다. 그 안에서는 점수 순.
4. `OTHER`(연예·스포츠)는 게시하지 않는다.
- 최소 점수는 11.5 로 낮췄다(배분이 큐레이션을 맡으므로). 단, 비트와 무관한 저품질은 여전히 탈락.

## 사건 중복 판정 (2026-11-06 추가)
제목 자카드 유사도 0.45만으로는 같은 사건을 놓친다(예: Cybertruck 중단 기사가 13분 간격으로 2번 게시됨).
`same_event()` = **최근 7일 제목의 토큰 문서빈도(df) 기반**. 희귀 토큰(df ≤ 전체의 2%)이 2개 이상 겹치면 같은 사건으로 보고 `note='dup'` 처리.
- "Tesla…Cybertruck…" vs "Tesla's Musk…Cybertruck…" → 자카드 0.19지만 dup ✓
- "Trump threatens tariffs on China" vs "Trump says China will buy soybeans" → 통과 ✓

**2026-11-08 보강 (사나 공습 3연타 사고)**: 희귀 토큰 규칙만으로는 부족했다. 예멘 사나 공습이
`At least 25 killed…` → `Airstrikes kill more than 2 dozen…` → `Saudi-led strikes kill at least 27…`
로 1시간 40분 사이 3번 게시됐다. 원인은 형태소가 안 묶인 것(kill/killed, strike/strikes, Houthi/Houthis)
+ 임계값이 자카드 0.45 하나뿐이었던 것.
- `toks()` 에 가벼운 어간 축약(`_stem`) 추가 — ing/ed/ies/ly/s 제거.
- 규칙 확장: 자카드 > 0.45 **또는** 공통 토큰 ≥ 4 **또는** 공통 토큰 ≥ 3 & 자카드 ≥ 0.15.
- 검증: 게시분 6시간 내 20쌍을 전부 잡고, 다른 사건(에티오피아 수출 vs GDP)은 통과. 예멘 타이즈 vs 사나처럼
  같은 전장의 다른 사건까지 묶이는 과잉 차단은 감수한다(같은 공습 3연타보다 낫다).

## 메시지 형태 (정리형)
```
<번역된 본문 2~4문장 — 무엇이 일어났고 배경이 무엇인지>

<blockquote expandable>출처
· 매체A
· 매체B</blockquote>
```
- **2문장, 220자 이내**가 기본 (Gram 요청 2026-11-06).
- 시각·분류·매체 메타 줄은 넣지 않는다 (2026-11-06 Gram 요청).
- 출처는 텔레그램 접히는 인용(blockquote expandable) 안에 넣는다.
- 링크 미리보기(큰 이미지)는 끈다 — `disable_web_page_preview=true`.
- 원문 제목을 그대로 붙이지 않는다. 본문 앞 2~3문단을 번역·중복 제거 후 해요체로 다듬는다(`writer.py`).
- 본문이 110자 미만이거나 제목을 되풀이하면 보도하지 않고 다음 후보로 넘어간다(`no_body`).
- Google News 링크는 실제 기사 URL로 해석(`writer.resolve_google`), 같은 사건 다른 매체는 `related()` 로 출처에 추가.
  `related()` 는 본문 출처와 **같은 매체를 다시 넣지 않는다**(2026-11-08 이전엔 Eagle-Tribune 이 두 번 찍혔다).
- **노이즈 필터** (`NOISE_TITLE`, 2026-11-08): 종목 추천("3 US Bank Stocks Built For…"), 애널리스트
  목표가("target price"), 팟캐스트·트랜스크립트 요약은 기사가 아니라 투자 광고이므로 `note='noise'` 로 버린다.
- 구글뉴스로 섞여 들어오는 잡음 매체(파이낸셜뉴스·news.az·BBN Times·teleSUR 등)는 `news_fetch.PRIORITY`
  에서 음수 가중치를 준다.


## 명령
```
python3 osint.py draft          # 후보 1건 → queue/*.json (모델 작성용)
python3 osint.py queue          # 대기 중 초안 목록
python3 osint.py send --id N --file msg.txt   # 작성한 본문 전송
```
```
cd /var/minis/shared/osint
sh keepalive.sh                 # 데몬 살아있는지 확인/재시작
python3 osint.py status         # 상태 요약
python3 osint.py collect        # 수동 수집
python3 osint.py tick --force   # 즉시 1건 보도
python3 osint.py audit          # 주간 점검 리포트 생성 (실패/정체/일시오류 구분)
python3 osint.py audit --send   # 개인 DM으로 전송
tail -20 daemon.log
```

## 파라미터 (config.json)
| 키 | 값 | 의미 |
|---|---|---|
| channel | @webpaperHQ | 보도 채널 |
| owner_chat | 8648671122 | 주간 보고 개인 DM |
| min_score | 11.5 | 보도 최소 점수 (배분이 큐레이션을 맡으므로 낮춤) |
| hot_score | 22 | HOT 레인(z≥4.0) 기준 |
| max_age_hours | 6 | 이 시간 지난 기사는 버림 |
| collect_every_s | 3600 | 수집 주기 |
| post_every_s | 300 | 보도 주기 (5분) |
| max_posts_per_hour | 10 | 시간당 상한 |

## 장애 대응
- 앱이 백그라운드로 내려가면 샌드박스가 죽어 데몬이 멈출 수 있다. **매시 07분 keepalive 스케줄 태스크**가 하트비트(15분)를 확인하고 자동 재시작한다.
- 재시작 시 밀린 기사는 6시간 규칙에 걸려 자동 폐기되므로, 오래된 뉴스가 몰려 나오지 않는다.
- 채널이 조용하면 `osint.py status`의 `last_post`와 `daemon.log`를 본다.

## 2026-11-07 정비 — 낭비·버그 목록
| 찾은 것 | 내용 | 조치 |
|---|---|---|
| 수집 윈도 과다 | `collect(hours=30)` 인데 `max_age_hours=6` → 버려질 기사의 4/5를 DB에 넣고 있었음 | `hours=8` 로 축소 |
| DB 무한 성장 | 오래된 미게시 기사가 계속 쌓여 매 선택마다 전체 스캔 | `prune()` 하루 1회, 7일 보관, WAL 체크포인트 동반 |
| df 전체 스캔 | 5분마다 2천 건 제목 재토큰화 | `df_map` 10분 TTL 캐시 |
| 하트비트 쓰기 | 30초마다 커밋 → WAL 하루 수천 회 | 60초에 1회로 (keepalive 임계 15분이라 안전) |
| 로그 무한 성장 | 로테이션이 keepalive 에만 의존 | 데몬이 스스로 200KB에서 100KB로 자름 |
| **큐 누수(버그)** | 3시간 지난 초안 파일을 지우면서 `note='queued'` 를 안 되돌려 그 기사가 영구 미게시로 남음 | 초안 삭제 시 `note=NULL` 복구 |
| 긴 줄 분할 | DM 보고에 3,800자 넘는 한 줄이 오면 전송 실패 | 강제 분할 추가 |
| **구글뉴스 URL 미반영(버그)** | 해석한 실제 기사 URL을 DB에만 쓰고 반환 튜플에는 옛 구글뉴스 링크를 넘겨 **본문 추출이 100% 실패** → 게시가 60분간 조용히 멈춤 | `_candidate_ok` 가 해석된 URL을 포함한 튜플을 반환 |
| 침묵 경로 | 락 대기·후보 없음이 로그 없이 지나가 원인 추적 불가 | `publish: busy` / `publish: 후보 없음` / `slow cycle Ns` 로그 추가 |
| **잘린 요약 게시(버그)** | 모델이 90자에서 끊긴 응답을 냈는데 그대로 채널에 나감(#36 "…그는 민"). 길이 하한 60자만 검사했음 | `llm.is_complete()` — 80자 미만이거나 문장부호로 끝나지 않으면 폐기 + 초안 유지. 게시 직전 한 번 더 검사 |

## 비용 구조 (2026-11-06 정리, 2026-11-07 재검토)| 쓰는 곳 | 모델 | 빈도 |
|---|---|---|
| 뉴스 요약 (데몬, 5분 주기) | **무료** Gemini 3 Flash | 게시할 때만 (최대 288/일) |
| `OSINT 감시봇 유지` | 기본 모델 (프리픽스 동일 → 캐시 히트) | 24/일 |
| `OSINT 보도 예비` | 기본 모델 | 12/일 |
| 주간 3종 + 피드 쓰기 | 기본 모델 | 주 10회 |

**결론: 무료 티어는 뉴스 파이프라인 전용으로 남긴다.** 36회/일 스케줄 세션을 무료 모델로 돌리면
(1) 같은 `GEMINI_API_KEY` 의 일일 한도를 뉴스 요약과 나눠 쓰게 되어 **제품이 먼저 멈춘다**,
(2) 기본 모델은 시스템 프롬프트 프리픽스가 같아 **컨텍스트 캐시가 히트**하므로 실제 비용이 이미 작다.
스케줄 세션을 옮겨서 아끼는 금액은 월 몇 천 원 수준 — 리스크에 비해 남는 장사가 아니다.

- 무료 모델은 앱에 등록돼 있다(`Gemini Free` / `gemini-3-flash-preview`). 필요할 때 `--model` 로 지정하거나 `minis-model-use` 로 쓴다.
- `llm.py` 폴백: Gemini → Cerebras → Mistral → Groq → OpenRouter (환경변수 키가 있는 것만).
- 폴백 동작: **429·한도 오류가 난 제공자는 30분 쿨다운**으로 건너뛴다(같은 실패를 매번 반복하지 않음).
- **UA 필수**: Groq·Cerebras 등은 파이썬 기본 User-Agent 를 Cloudflare 1010 으로 403 차단한다. `_post()` 가 브라우저 UA 를 붙인다.
- Groq 사용 가능 모델(2026-11 확인): `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `openai/gpt-oss-20b`. `llama-3.3-70b-versatile` 은 이 계정에 없음(404).
- **실제 지출은 [사용량](minis://settings/usage) 에서 확인**한다. 감시봇 회차가 눈에 띄게 크면 그때 다시 판단.
- **주의**: Gemini 무료 티어 모델명은 자주 바뀐다. `gemini-3.8/3.7/3.5-flash`, `gemini-2.5-flash` 는 이 계정에서 404. 실제로 되는 것: `gemini-3-flash-preview`, `gemini-flash-latest`, `gemini-3.1-flash-lite`.

## 주간 루틴 (일요일, Asia/Seoul)
| 시각 | 태스크 | 하는 일 | 산출물 / 보고 |
|---|---|---|---|
| 01:00 | **주간 꿈꾸기** | 지난 7일 memory·STATE·daemon.log·태스크 실행결과를 4덩어리로 응축 | `shared/dreams/weekly-YYYY-MM-DD.md` + memory 1항목 (채팅) |
| 04:00~04:30 | **주간 자유시간** | 자기 점검 → 인터넷 조사 → 개선 1~2개 실제 반영. 정해진 보고서 없음 | `shared/journal/YYYY-MM-DD.md` + **텔레그램 DM** + 채팅 5줄 |
| 08:00 | **OSINT 주간 점검** | 데몬·태스크 상태, 보도 품질 표본 검토, 소스 생존/교체, audit DM | **텔레그램 DM**(요약 보고문 + audit 표) + 채팅 피드백 |

### DM 보내는 방법
```
cd /var/minis/shared/osint && python3 osint.py notify --file /tmp/report.txt
```
- 기본 수신자 `config.json` 의 `owner_chat` (Gram 개인 DM). `--chat @channel` 로 바꿀 수 있다.
- HTML 허용, 3,800자 단위로 자동 분할, HTML이 깨지면 평문으로 재시도.
- 공개 채널(@webpaperHQ)에는 뉴스만 보낸다. 운영 보고는 반드시 DM.

- 셋 다 `--target new` (새 채팅). 실패한 회차는 다음 주 점검 때 `lastResultPreview` 로 확인한다.
- 자유시간 원칙: 외부 전송·게시·삭제·로그인 금지. 확신 없는 변경은 후보로만 남긴다.

## 주간 점검 (일요일 08:00)
1. `sh keepalive.sh` 로 데몬 상태 확인
2. `python3 osint.py audit --send` → 개인 DM으로 리포트 (소스 생존/정체/실패, 7일 수집·보도량, 많이 잡힌 출처)
3. 채팅에 요약 + **Gram이 직접 해줘야 할 일** 정리
4. 새 소스 후보 제안

## 무료지만 사용자 손이 필요한 것 (후보)
| 자원 | 용도 | 비고 |
|---|---|---|
| NewsAPI.org 키 | 뉴스 검색 폭 확대 | 무료 100req/day |
| GNews.io 키 | 유사 | 무료 100req/day |
| ACLED 액세스 키 | 분쟁 사건 데이터(사상자·충돌 좌표) | 무료 연구용 등록 |
| UCDP API | 분쟁 사망자 통계 | 무료 |
| NASA FIRMS 키 | 화재·폭발 열점 탐지 | 무료 |
| Shodan / Censys 키 | 인프라 스캔(OSINT 심화) | 무료 티어 |
| Telegram 채널 목록 | 현지 1차 소스(우크라·중동) | 구독 채널명 필요 |

## 본문을 못 가져오는 기사 (2026-11-08 정책)
- 페이월·JS 렌더링 매체(FT·NYT·Reuters·MarketWatch·Seeking Alpha·Motley Fool 등)는 본문 추출이 실패한다. 실패율 실측: `no_body` 226건 중 **180건이 RSS 스니펫 150자 미만**이었다.
- 3단 처리: ① 본문 300자 이상 → 2문장 요약 ② 스니펫 150자 이상 → 스니펫 기반 요약 ③ **스니펫 55자 이상 + score≥13 → `thin` 초안**(2문장·180자, 스니펫 밖 사실 금지 프롬프트, `is_complete` 하한 60자) ④ 그 외 `no_body`로 폐기.
- `thin` 초안은 `queue/<id>.json` 의 `thin: true` 로 표시되고 `llm.summarize(..., thin=True)` 로 다른 프롬프트를 쓴다. 본문이 짧으니 배경 설명을 지어내지 못하게 막는 것이 핵심.
- 남은 실패는 진짜 페이월(전문 유료)뿐이다. 그런 매체만 따로 빼고 싶으면 sources.txt 에서 prio 를 낮춘다.

## 시간당 상한 (2026-11-08 수정)
- 이전에는 `max_posts_per_hour` 검사가 `tick()`(기계 작성 경로)에만 있었고 llm 경로에는 없어서 실측 최대 **13건/시간**(상한 10). 155개 발행 구간 중 42개가 초과였다.
- `cap_hit(con, cfg)` 로 통합 — llm 경로·모델 경로 모두 게시 전에 검사한다. 초과 시 로그에 `cap: N건/1h` 를 시간당 1회만 남긴다.
