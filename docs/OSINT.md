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
| `gh workflow run osint.yml -f minutes=230` | 수동 | 예약이 며칠 죽었을 때 4시간 한 번에 |

⚠️ 러너를 하루 종일 쓰는 구조는 GitHub 의 "프로젝트와 무관한 연산" 조항과 거리가 있다.
문제가 되면 블록을 줄이고 주기를 늘리거나 VPS(월 $4~6)로 옮긴다.

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

## 중복 판정

제목 자카드 0.45만으로는 부족해 `same_event()` 를 쓴다.
- `toks()` 에 어간 축약(`_stem`: ing/ed/ies/ly/s) — kill/killed, strike/strikes, Houthi/Houthis 를 묶는다.
- 판정: 자카드 > 0.45 **또는** 공통토큰 ≥ 4 **또는** 공통토큰 ≥ 3 & (사상자 어휘 | 자카드 ≥ 0.30)
  **또는** 희귀토큰(df ≤ 2%) 2개 이상.
- 과잉 차단은 감수한다(같은 공습 3연타보다 낫다). `dup:` 로그로 추적한다.

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

## 이전 이력 (요약)

- 2026-11-06: 폰 `workspace/` → `shared/osint/` 이사, SQLite 상태.
- 2026-11-07: 단일 점수 랭킹 → 비트 분류·균형 배분, 중복 판정 강화, 무료 LLM 폴백.
- 2026-11-08: 지역 축 + 품질 필터, 폰 실행 폐기 → **GitHub Actions 이전**.
