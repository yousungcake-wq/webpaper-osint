# WebPaper 사이트 — 정적 피드로 운영하기

> 사이트: <https://yousungcake-wq.github.io/webpaper-osint/>
> 코드: `site/index.html` (한 파일) · `feedgen.py` (데이터 생성) · `data/feed.json` (생성물)
> 만든 날 2026-11-10. 텔레그램 보도 파이프라인은 그대로 두고 **출력 하나를 더 붙였다.**

---

## 1. 한 장 그림

```
cron-job.org (5분)
   └─▶ GitHub Actions: run_once.py
          ├─ 수집 → 분류 → 선택 → LLM(제목+요약)
          ├─ 텔레그램 채널 게시           ← 기존 출력
          ├─ state/posts/<날짜>/*.jsonl  ← 게시 1건 = 파일 1개
          ├─ feedgen.py → data/feed.json ← 새 출력(사이트 데이터)
          └─ git commit & push (state + data)

GitHub Pages  ── site/index.html (정적 1장, CSS·JS 인라인)
                     │  fetch(?t=타임스탬프)
                     ▼
              raw.githubusercontent.com/.../data/feed.json
```

핵심 결정: **사이트(HTML)와 데이터(feed.json)를 분리해서 배포한다.**
HTML 은 `site/**` 가 바뀔 때만 Pages 로 배포하고, 데이터는 저장소 파일로만 갱신한다.
같이 배포하면 5분마다 Pages 빌드가 돌아 **시간당 10회 빌드 한도**에 걸리고, 초과분은
조용히 스킵되어 사이트가 낡은 채로 남는다.

---

## 2. 무료 티어로만 돌아가는 이유

| 쓰는 것 | 무료 조건 | 이 프로젝트의 사용량 |
|---|---|---|
| GitHub Actions | **공개 저장소는 무료·무제한** | 하루 288회 × 20~50초 |
| GitHub Pages | 공개 저장소, 월 100GB 전송(소프트), 사이트 1GB | HTML 20KB 1장 |
| raw.githubusercontent.com | 무료, CDN, `max-age=300` | 5분마다 게시 → 방문자가 60초 폴링 |
| LLM 요약 | Gemini·Groq 무료 한도 | 게시 1건당 1콜 (제목+요약 동시) |
| 도메인 | 기본 `*.github.io` | 0원 (커스텀 도메인은 연 1~2만원) |

서버·DB·CDN 비용 0. **정적 호스팅 + 파일 하나**면 충분한 구조라서 그렇다.
실시간으로 보이지만 실제로는 "5분마다 갱신되는 파일을 브라우저가 다시 읽는" 방식이다.

---

## 3. 데이터 계약 (`data/feed.json`)

```json
{ "generated_at": "2026-11-10T10:10:41Z", "count": 240, "days": 5,
  "items": [
    { "h": "카타르, 호르무즈 긴장 속 LNG 선적량 확대",   // 한국어 제목(없으면 "")
      "t": "Qatar LNG Loadings Gain Pace",              // 원문제목(폴백·상세화면 표기)
      "summary": "카타르가 … 늘리며 …",                  // 텔레그램에 쓴 그 요약
      "url": "https://…", "src": ["Rigzone"],
      "b": "COMMODITY", "r": "MIDDLE_EAST",
      "ts": "2026-11-10T09:41:00Z" } ] }
```

- 최근 **5일**, 최대 **300건**. 넘으면 오래된 것부터 버린다.
- `b`(비트) 6종: TECH·FINANCE·CONFLICT·ECONOMY·COMMODITY·POLITICS / 모르면 `""`
- `r`(지역) 7종: US·LATAM·EUROPE·ASIA·MIDDLE_EAST·AFRICA·GLOBAL / 모르면 `GLOBAL`
- 점수(`s`)와 원시 시각은 **일부러 뺐다**. 화면에 안 쓰는 값을 5분마다 커밋하면
  저장소만 무거워진다(현재 240건 ≈ 165KB).
- 파일은 `feedgen.write()` 가 `.tmp` 에 쓰고 `os.replace` 로 교체한다(중간 상태 없음).

---

## 4. 제목은 어디서 오나

사이트는 한국어 제목이 필요한데 파이프라인의 원제목은 영어다. 제목을 따로 부르면
무료 쿼터가 두 배로 들기 때문에 **요약 호출 한 번에서 둘 다 받는다**
(`llm.summarize_parts` → `prompt_for(..., head=True)`).

- 출력 형식: 첫 줄 `제목: …`, 둘째 줄부터 요약. `split_head()` 가 나눈다.
- 모델이 형식을 안 지켜도 요약은 살린다: `제목 없음` → 사이트는 원제목으로 폴백,
  JSON 응답·굵게·따옴표·마침표 붙은 제목까지 방어(테스트 `/tmp` 아닌 수동 확인).
- 제목이 잘렸거나(80자 미만 요약) 이상하면 **다음 제공자로 넘어간다**(`_generate(accept=…)`).
- 2026-11-10 이전 236건은 `backfill_head.py` 로 한 번에 채웠다(제목만 생성, 요약은 보존).

---

## 5. 운영

| 하고 싶은 것 | 방법 |
|---|---|
| 디자인 고치기 | `site/index.html` 수정 → push. `site/**` 가 바뀌면 Pages 가 자동 배포(≈40초) |
| 필드 추가 | `feedgen.build()` 에 키 추가 → `site/index.html` 에서 읽기 → push |
| 데이터 즉시 재생성 | `python3 feedgen.py` (로컬) 또는 다음 회차를 기다림 |
| 배포 다시 하기 | Actions → `site (GitHub Pages)` → Run workflow |
| Pages 끄기 | Settings → Pages → Unpublish |

### 손대면 안 되는 것

- **Pages 소스를 `main` 브랜치(branch 빌드)로 바꾸지 말 것.** 그러면 `state/**` 커밋마다
  빌드가 걸려 시간당 10회 한도에 걸린다. 현재는 `build_type=workflow`(Actions 배포)다.
- **`data/feed.json` 은 생성물이다.** 손으로 고치면 다음 회차에 덮인다.
  로컬에서 여러 번 커밋하면 이 파일만 충돌한다 → 충돌 시 `feedgen.py` 를 다시 돌려
  해결하고 `data` 는 되도록 커밋하지 않는다(파이프라인이 알아서 올린다).

---

## 6. 알려진 한계

1. **최대 5~10분 지연.** raw CDN 캐시(`max-age=300`) + 회차 주기(5분). 브라우저는
   `?t=Date.now()` 로 CDN 캐시를 비껴가지만, 원본 파일 자체가 5분마다 바뀐다.
2. **`*.github.io` 주소.** 커스텀 도메인을 붙이면 `yousungcake-wq.github.io` 경로가 사라진다
   (CNAME 한 줄 + DNS). Pages 설정에서 도메인만 넣으면 된다.
3. **사이트가 GitHub 에 묶인다.** 저장소가 막히거나 Pages 가 정지하면 사이트도 멈춘다.
   대안은 Cloudflare Pages(무료·전송 무제한)로 HTML 만 옮기고 데이터는 그대로 raw 를
   읽게 하는 것 — 데이터 계약이 같아서 `FEED_URL` 한 줄만 바꾸면 된다.
4. **검색이 없다.** 240건이 5일 창을 돌기 때문에 검색·아카이브가 필요해지면 별도 저장이
   필요하다(현재 state 파일이 곧 아카이브지만 5일 뒤 삭제된다).
5. **개별 기사 페이지가 없다.** 공유는 사이트 첫 화면 링크 또는 원문 링크로만 된다.
   SEO·공유 미리보기를 원하면 기사별 HTML 생성이 다음 단계다.

---

## 7. 다음 후보 (싶으면 말해라)

1. **기사별 페이지 + OG 카드** — `site/a/<id>.html` 을 feedgen 이 같이 생성(Pages 재배포 필요 → 하루 1회 배치로).
2. **RSS 출력** — `data/feed.xml` 한 파일. 다른 리더로도 구독 가능.
3. **커스텀 도메인** — `webpaper.xxx`.
4. **검색·아카이브** — 90일 보관 + 정적 인덱스(JSON) 로 클라이언트 검색.
5. **PWA** — 홈 화면 추가 + 오프라인 캐시.
