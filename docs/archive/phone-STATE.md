# OSINT 상태 (2026-11-08 08:10 KST) — 주간 점검 반영

## 지금 상태
- 데몬: `python3 osint.py daemon`, cwd `/var/minis/shared/osint`, pid 는 `daemon.pid` (점검 중 재기동)
- 수집 1시간 / 보도 5분(mode=llm, Gemini→Cerebras→Mistral→Groq→OpenRouter) / 시간당 상한 10건
- DB: items 6,958 · 실제 게시 189 (11-06 54 · 11-07 135) · note 분포: expired 788 · no_body 226 · dup 102 · no_direct_url 21
- 소스: **87개** (실패 0 — 11-08 점검에서 '실패 8'은 전부 DNS 순간오류로 판명)
- 스케줄 태스크 6개: `OSINT 감시봇 유지`(매시 07분) · `OSINT 보도 예비`(2시간 12회) · `OSINT 주간 점검`(일 08:00)
  · `주간 꿈꾸기`(일 01:00) · `주간 자유시간`(일 04:00) · `피드 쓰기`(매일 08:00)

## ⚠️ 알려진 구조적 한계 (버그 아님)
**야간 게시량은 앱이 깨어 있는 시간에 묶여 있다.** 11-07 18:00 UTC 이후 1~4건/시간으로 떨어진 것은
코드 문제가 아니라 앱이 백그라운드로 내려가면 PRoot 샌드박스가 함께 멈추기 때문이다(데몬이 freeze).
증거: 같은 코드로 주간에는 10~12건/시간, 야간에는 1건/시간. `slow cycle 1194s` 는 멈췄던 시간이 그대로 찍힌 것.
keepalive 태스크가 매시 :07 에 앱을 깨워 1건씩은 나가지만, 그 이상은 불가능하다.

## 2026-11-08 주간 점검에서 고친 것
1. **사나 공습 3연타** — `toks()` 어간 축약 + `same_event()` 공통토큰 규칙(≥4 또는 ≥3&자카드≥0.15).
2. **출처 중복 표기** — `related()` 가 본문 출처와 같은 매체를 다시 넣던 것 수정 (Eagle-Tribune 2회).
3. **투자 광고 차단** — `NOISE_TITLE` 로 종목추천·목표가·팟캐스트 요약을 `note='noise'` 처리.
4. **잡음 매체 감점** — `news_fetch.PRIORITY` 에 파이낸셜뉴스·news.az·BBN Times·teleSUR 등 음수 가중치.
5. **감사 오탐** — `audit` 이 DNS 순간오류(Errno -3)를 '실패'로 세던 것을 3회 재시도 + `일시오류` 분리.
6. 소스 81 → **87** (Semafor·New Lines·Sahel Intelligence·Bellingcat·Rest of World·TechCabal).

## 다음 후보
- 야간 공백을 줄이려면 앱이 살아 있어야 한다 → 스케줄 태스크 주기를 늘리는 것 말고는 방법이 없다(비용 발생).
- `Spiegel EN`(최신 11-03) 정체 — 영어 RSS 갱신이 뜸하다. 교체 후보: Crisis Group(주 1회 발행).
- 예멘 타이즈 vs 사나처럼 같은 전장의 다른 사건이 과잉 차단될 수 있다. 표본 20쌍에서는 문제 없었음.

## 복구 순서 (새 세션에서)
1. `cd /var/minis/shared/osint && sh keepalive.sh`
2. `tail -20 daemon.log && python3 osint.py status`
3. 문제 있으면 `OSINT.md` → `/var/minis/shared/dreams/weekly-2026-11-08.md`
