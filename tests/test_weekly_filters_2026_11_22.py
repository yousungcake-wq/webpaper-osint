#!/usr/bin/env python3
"""주간 점검(2026-11-22) 👎 사유 반영 — 회귀 테스트 (네트워크 불필요).

왜 있나: 11-15 에 스포츠·의례·임상 기사가 그대로 게시됐다. 규칙은 이미 있었지만
  · SPORT 는 리그명('afcon','nfl')만 알고 팀 애칭('Super Eagles')·동작어('a swim')를 몰랐고,
  · 사과·MOU·'협상 모색'·의상 같은 **자리(ceremony)** 는 규칙 자체가 없었다.
실측(11-22): 최근 게시 327건 중 0.6% 만 차단, 👍 오탐 0/10.

  python3 tests/test_weekly_filters_2026_11_22.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import filters  # noqa: E402

FAIL = []


def blocked(title, expect, why):
    got = filters.topic_block(title) or filters.is_digest(title, "")
    if expect and not got:
        FAIL.append(f"막아야 하는데 통과: {title!r} ({why})")
    elif not expect and got:
        FAIL.append(f"통과해야 하는데 막힘({got}): {title!r} ({why})")


# ── 막아야 한다: 👎 실제 사례 ──────────────────────────────────────────
# 스포츠: 리그명 없이 팀 애칭·종목 동작어만 있는 제목
blocked("How injury denied me Super Eagles debut – Zadok Yohanna", True, "👎#1588 축구")
blocked("Detroit Lions crushed the New England Patriots in Munich, Germany, "
        "for a fifth straight win", True, "👎#1755 NFL")
blocked("Palestinians reclaim Gaza’s shores with a swim for hope", True, "👎#2125 수영")
blocked("Junior Chipolopolo Arrive in Burkina Faso", True, "👎#950 축구")

# 외교·행사 의례: 사건이 아니라 자리
blocked("EU leaders seek to tackle challenges at first ever AI summit", True, "👎#1584")
blocked("IMF chief wears golden Thai silk at ASEAN Summit, drawing praise from Anutin", True, "👎#1599")
blocked("Indonesia’s Prabowo apologises to Singapore, Malaysia over transboundary haze", True, "👎#1627")
blocked("Somalia, Türkiye sign agreement to strengthen family protection and social services", True, "👎#2025")
blocked("Brazil and US Eye Talks Before Miami Summit", True, "👎#2341")

# 기업 임상·자금조달 디테일
blocked("FDA Doesn't Approve Summit's China-Developed Lung Cancer Drug", True, "👎#1955")
blocked("Lytix Biopharma raises $8.36M post-IPO to prep melanoma Phase III", True, "👎#2059")

# 확인되지 않은 경고·주장
blocked("Warning of Suspected Chemical Weapons Storage at Sudanese Army Facilities", True, "👎#1600")
blocked("Israel, Taiwan pose threat to Somalia’s existence: Somali president", True, "👎#1623")

# ── 통과해야 한다: 진짜 사건 (오탐이 나면 규칙이 너무 넓다) ─────────────────
# 'says' 만으로 막으면 이 셋이 죽는다 — 11-22 실측 327건 중 38건이 이 부류였다.
blocked("Trump says no rush on Iran decision, looks to next year", False, "실제 결정 보도")
blocked("Iran says it downed US drones over Hormuz as bid to end war remains stalled", False, "실제 교전")
blocked("Yemen's Houthis tell airlines to avoid Saudi airspace as attacks coming", False, "실제 위협")
blocked("US denies Iranian account that missile damaged F-16 over Gulf", False, "실제 사건 부인")
# cyber 사고 사과는 사건이다
blocked("Coles investigates cyber-attack and apologises to customers", False, "👍 사건 사과")
# IPO 자체는 사건이다 (규칙을 'ipo' 로 넓히면 이게 죽는다)
blocked("Anthropic raises $93.5 billion total as underwriters scoop up ‘greenshake’ "
        "overallotment".replace("greenshake", "greenshoe"), False, "대형 IPO 사건")
# 스포츠가 아닌 단어
blocked("Eight killed, others injured in multiple-vehicle crash on Niger road", False, "사고 사망")
blocked("China’s AI chipmakers report first profits, but will the trend continue?", False, "산업 뉴스")

if FAIL:
    print(f"FAIL {len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ok — 주제 게이트 회귀 18건 통과")
