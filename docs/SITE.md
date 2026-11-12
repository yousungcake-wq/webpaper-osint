# WebPaper 사이트 — 정적 피드로 운영하기

> 사이트: <https://yousungcake-wq.github.io/webpaper-osint/>
> 코드: `site/index.html` · `site/search.html` · `site/feed.json`+`site/feed-p*.json`(생성물) · `feedgen.py`(생성기)
> 만든 날 2026-11-10. 텔레그램 보도 파이프라인은 그대로 두고 **출력 하나를 더 붙였다.**

---

## 1. 한 장 그림

```
cron-job.org (5분)
   └─▶ GitHub Actions: run_once.py
          ├─ 수집 → 분류 → 선택 → LLM(제목+요약)
          ├─ 텔레그램 채널 게시              ← 출력 1
          ├─ state/posts/<날짜>/*.jsonl     ← 게시 1건 = 파일 1개
          ├─ feedgen.py → site/feed.json+feed-pN.json ← 출력 2(사이트 데이터)
          └─ git commit & push (state + site)
                    │
                    ▼  같은 워크플로 안에서 이어서
          job: deploy → Pages 배포(≈30초)   ※ site.yml 은 사람이 site/ 를 고칠 때만
                    │
                    ▼
   site/index.html ── fetch ./feed.json → 아래로 내려가면 ./feed-pN.json
```

**핵심: 데이터를 사이트와 같이 배포한다.** 처음엔 데이터만 따로 raw 로 빼려 했지만
(5분마다 배포하면 Pages 빌드 한도에 걸린다고 생각해서), GitHub 문서를 확인하니

> "GitHub Pages sites have a soft limit of 10 builds per hour.
> **This limit does not apply if you build and publish your site with a custom GitHub Actions workflow.**"
> — docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits

이었다. 그래서 같은 출처에서 `./feed.json` 을 읽는다. CORS 문제도, CDN 5분 캐시도 없다.
Pages 는 **배포할 때 CDN 캐시를 비우므로** 게시 → 커밋 → 배포가 끝나면 즉시 새 데이터다.
실측: 게시·커밋 11:05:33 → 배포 포함 회차 종료 11:06:17 → 라이브 피드 일치 확인 11:06:51.

### 배포를 `osint.yml` 안에서 하는 이유 (함정 2개)

1. **`GITHUB_TOKEN` 으로 푸시한 커밋은 다른 워크플로를 트리거하지 않는다**(재귀 방지).
   그래서 `site.yml` 의 `paths: site/**` 필터로는 봇의 커밋에 배포가 붙지 않는다.
   → `osint.yml` 에 `deploy` 잡을 넣고 `needs: cycle` + `if: changed` 로 잇는다.
2. **`actions/checkout` 의 기본 ref 는 이벤트 시점 SHA.** 그대로 두면 방금 커밋된 피드가 아니라
   **직전 커밋**을 배포한다(11:00 실측: live 가 10:50 피드로 갱신됨). → `with: ref: main`.

---

## 2. 무료 티어로만 돌아가는 이유

| 쓰는 것 | 무료 조건 | 이 프로젝트의 사용량 |
|---|---|---|
| GitHub Actions | **공개 저장소는 무료·무제한** | 하루 288회 × 20~50초 + 배포 288회 × ~30초 |
| GitHub Pages | 공개 저장소, 월 100GB 전송(소프트), 사이트 1GB, **Actions 배포는 빌드 한도 없음** | HTML 35KB + feed.json 170KB |
| LLM 요약 | Gemini·**NVIDIA NIM**·Groq 무료 한도 | 게시 1건당 1콜 (제목+요약 동시) + 분류 1콜/회차 |
| 도메인 | 기본 `*.github.io` | 0원 (커스텀 도메인은 연 1~2만원) |

서버·DB·CDN 비용 0.

### 시도했다가 버린 것 (다시 하지 말 것)

| 방법 | 결과 |
|---|---|
| raw.githubusercontent + `?t=` 캐시 버스팅 | **쿼리스트링이 캐시 키가 아니다.** `max-age=300` 그대로 5분 낡음 |
| raw 에 `Cache-Control: no-cache` 요청 | 무시됨(HIT) |
| jsDelivr + purge API | purge 는 성공 응답을 주지만 **원본(raw)이 5분 낡아서** 갱신이 안 됨 |
| 릴리스 자산 다운로드 URL | 내용은 즉시 최신이지만 302 응답에 **CORS 헤더가 없어** 브라우저에서 못 읽음 |
| GitHub API `contents` | 60초 캐시 + CORS OK 지만 **IP당 시간당 60회** 제한 |
| ntfy.sh 푸시 | 동작은 함(SSE·CORS OK). 지금 구조에선 필요 없어 보류 — 즉시 push 가 필요해지면 후보 |

---

## 3. 데이터 계약 (`site/feed.json` + `site/feed-pN.json`)

```json
{ "generated_at": "2026-11-10T10:42:55Z", "count": 300,
  "page": 0, "pages": 2, "total": 547, "days": 5,
  "items": [
    { "h": "카타르, 호르무즈 긴장 속 LNG 선적량 확대",   // 한국어 제목(없으면 "")
      "t": "Qatar LNG Loadings Gain Pace",              // 원문제목(폴백·상세화면 표기)
      "summary": "카타르가 … 늘리며 …",                  // 텔레그램에 쓴 그 요약
      "url": "https://…", "src": ["Rigzone"],
      "b": "ECONOMY", "r": "MIDDLE_EAST", "s": 18.4,
      "ts": "2026-11-10T09:41:00Z" } ] }
```

- 최근 **5일 전체**를 **300건 단위 쪽**으로 나눠 쓴다.
  `feed.json`=0쪽(첫 화면이 받는 최신 300건), `feed-p1.json`=그다음 300건, …
  사이트는 아래로 내려갈 때 다음 쪽을 그때 받는다. 쪽 수가 줄면 옛 `feed-pN.json` 은 지운다.
  - 2026-11-12 이전에는 한 파일 300건 상한이었다 → 하루 180~220건 게시라 **300건이 1.5일치**뿐,
    11-11 07:36 이 "마지막 기사"처럼 보였다(텔레그램에는 더 있는데). 지금은 5일 창 끝까지 내려간다.
- `b`(비트) 5종: **TECH·ECONOMY·CONFLICT·POLITICS·OTHER(기타)**.
  - 2026-11-10 통합: 금융(FINANCE)·원자재(COMMODITY) → ECONOMY.
  - **OTHER = "명확히 분류되지 않는 국제 뉴스"**(정상회의·외교 접촉·인물 동향·국제기구). 쓰레기 통이 아니다.
  - **POLITICS 는 좁게**: 선거·의회·정당·내각. 외교 일정은 POLITICS 가 아니라 OTHER.
  - OTHER 도 **게시한다**(예전엔 후보에서 잘라내 '기타'가 영원히 비어 있었다). 목표비 10건 중 2건.
  - 배지 색: 테크 파랑 · 경제 초록 · 분쟁 빨강 · **정치 보라** · 기타 회색(2026-11-12, 정치/기타 구분).
- `r`(지역) 7종: US·LATAM·EUROPE·ASIA·MIDDLE_EAST·AFRICA·GLOBAL / 모르면 `GLOBAL`
- `s`(중요도)는 속보 티커 정렬에만 쓴다. 원시 시각은 **일부러 뺐다**(`ts` 로 대체).
- `feedgen.write()` 는 쪽마다 `.tmp` 에 쓰고 `os.replace` 로 교체한다(중간 상태 없음).

---

## 4. 화면 기능

| 기능 | 구현 |
|---|---|
| 상단 패널 | 로고 · 테마(라이트/다크) · 검색 — **이 3개만** |
| 비트 필터 | 메인 페이지 칩 1개 선택(전체/테크/경제/분쟁/정치/기타) |
| 검색 | `search.html` — 돋보기 아이콘으로 이동. **한국어 친화**: 공백 토큰 **AND**('이란 트럼프' → 둘 다 있는 글), 조사 제거('이란은'→'이란'), **초성 검색**('ㅇㄹ'→'이란'), 180ms 디바운스, `<mark>` 하이라이트(겹치는 구간 병합, textNode 만 조작 → XSS 없음) |
| 정렬 | 결과 바의 **세그먼트 버튼**(최신순/관련도순). 네이티브 `<select>` 팝업을 쓰지 않는다 |
| 결과 탭 | 원문으로 바로 튀지 않고 **아래에서 상세 시트**가 올라온다(메인과 동일). 시트의 `기사 읽기` 로 원문 이동 |
| 지역 필터 | 검색 페이지에서 다중 선택(북미·중남미·유럽·아시아·중동·아프리카·국제) |
| 정렬 | 최신순 / 관련도순(제목 매치 10점, 요약 4점, 등장 횟수 가중) |
| 공유 | `#q=이란&b=CONFLICT&r=아시아,MIDDLE_EAST&s=relevance` — URL 로 필터 상태 재현 |
| 최근 검색어 | localStorage 8개, 검색창 포커스 시 패널 |
| 카드 표기 | `지역 · 매체` (예: `아시아 · CNA World`), 상대시각, 비트 pill |
| 상세 | 하단 시트(스와이프로 닫기), 한국어 제목 + 원문제목 + 요약 + 출처 + 원문 링크 |
| 성능 | 40건씩 증분 렌더 + IntersectionObserver 자동 추가, 카드에 `content-visibility:auto`(화면 밖은 레이아웃·페인트 생략), 스켈레톤 3~6장(모바일 3 / 데스크톱 6) |
| 갱신 | 60초 폴링, 데이터가 8분보다 낡으면 20초로 단축 |
| 입력 안전 | 한글 IME 조합 중 Enter 무시, 키보드 '검색' 키가 keydown+submit 으로 두 번 와도 1회 처리. 마지막 어절이 중복 입력된 경우('이란 트럼프트럼프')는 직전 질의로 잘라낸다 |
| 새 글 알림 | **배지 하나만**. 맨 위(스크롤<80px)면 배지를 띄우지 않고 바로 반영, 아래를 보고 있으면 배지 → 맨 위로 오르면 사라진다 |
| 상대시각 | 30초마다 `n분 전`을 다시 계산해 텍스트만 교체(재렌더 없음) |

---

## 5. 운영

| 하고 싶은 것 | 방법 |
|---|---|
| 디자인 고치기 | `site/index.html` 수정 → push. 자동 배포(≈30초) |
| 필드 추가 | `feedgen.build()` 에 키 추가 → `site/index.html` 에서 읽기 → push |
| 데이터 즉시 재생성 | `python3 feedgen.py` (로컬) 또는 다음 게시를 기다림 |
| 배포 다시 하기 | Actions → `site (GitHub Pages)` → Run workflow |
| Pages 끄기 | Settings → Pages → Unpublish |

### 손대면 안 되는 것

- **Pages 소스를 `main` 브랜치(branch 빌드)로 바꾸지 말 것.** 지금은 `build_type=workflow`.
  branch 빌드는 시간당 10회 한도의 적용을 받고, Actions 배포는 받지 않는다.
- **`site/feed*.json` 은 생성물이고 저장소에 커밋하지 않는다**(2026-11-12 변경).
  배포 잡이 체크아웃 직후 `python3 feedgen.py` 로 state 에서 만들고 그대로 Pages 에 올린다.
  커밋하던 시절에는 5분마다 500KB 를 다시 쓰고, 생성물이라 rebase 충돌이 났다.
  로컬에서 여러 번 커밋하면 이 파일만 rebase 충돌이 난다 → `feedgen.py` 재실행으로 해결.
- **`run_once.commit_state()` 의 `git add state site`** 를 `site` 없이 되돌리면
  사이트가 갱신되지 않는다(배포 트리거가 사라진다).

---

## 6. 알려진 한계

1. **지연 ≈ 게시 후 1분.** 게시 → 커밋 → 배포(15~35초) → 브라우저 폴링(최대 60초).
   더 줄이려면 push 구조(ntfy SSE 등)가 필요하다.
2. **배포 288회/일.** 공개 저장소라 비용은 0이지만, Actions 사용량이 늘고 배포 큐가 쌓이면
   (한 번에 하나씩) 지연이 늘어난다. 지금은 게시 간격이 6분~라 문제 없음.
3. **`*.github.io` 주소.** 커스텀 도메인은 CNAME 한 줄.
4. **5일 창.** 5일이 지난 기사는 사라진다(검색·아카이브 없음).
5. **개별 기사 페이지 없음.** 공유는 첫 화면 또는 원문 링크.
6. **LLM 한도에 사이트도 묶인다.** 무료 키가 429/503 이면 게시가 멈추고 사이트도 조용해진다.
   2026-11-10 제목 백필(236콜)로 Gemini 일일 한도를 소진해 게시가 멈췄고, 그날 NVIDIA NIM(GLM-5.3-flash)을
   두 번째 폴백으로 붙여 복구했다(요약 3~8초, 한국어 품질 양호).

---

## 7. 다음 후보

1. **기사별 페이지 + OG 카드** — 공유·검색 노출. `feedgen` 이 `site/a/<id>.html` 생성.
2. **커스텀 도메인**.
3. **90일 아카이브 + 검색 인덱스**(현재 검색은 5일 창 안에서만).
4. **RSS 출력** — `site/feed.xml`.
5. **PWA** — 홈 화면 추가, 오프라인 캐시.
