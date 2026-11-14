#!/usr/bin/env python3
"""나이지리아 내수 정치·심판 규칙 회귀 테스트 (2026-11-15, 네트워크 불필요).

계기: 2026-11-14 👎 3건(#1429 #1413 #819 — #736 은 아래 '남은 구멍' 참고)이 전부
나이지리아 내수 정치 발언 기사였다. 사용자 지시(2026-11-10)는 '아프리카는 분쟁만'인데,
비트 분류가 '호송대 투석 부인'을 CONFLICT 로 주는 바람에 게이트를 통과했다.
같은 날 👎 #1409 는 스포츠 심판 칭찬 기사였고 SPORT 목록에 'referee' 가 없었다.

이 테스트는 "무엇을 막는가"만큼 **무엇을 막지 않는가**를 고정한다 — 아프리카 분쟁
보도(사망·습격·폭격)와 다른 나라의 정치 기사는 그대로 통과해야 한다.

  python3 tests/test_ng_politics.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import filters  # noqa: E402

FAIL = []


def check(name, got, want):
    if got == want:
        print(f"ok {name}")
    else:
        print(f"FAIL {name}: got={got!r} want={want!r}")
        FAIL.append(name)


# ── 1. 👎 가 실제로 막히는가 ────────────────────────────────────────────
check("nd_1429_amaechi",
      filters.topic_block("Governor’s aide flays Amaechi over posture at emir’s palace"),
      "ngpolitics")
check("nd_1413_convoy_denial",
      filters.topic_block("Tinubu Aide Denies Convoy Stoning in Abuja"),
      "ngpolitics")
check("nd_819_can_win_votes",
      filters.topic_block("Nigeria: Tinubu Can Win Votes Without Visiting All 36 States - Segalink"),
      "ngpolitics")
check("nd_follow_up_rally",
      filters.topic_block("Stop following Tinubu to rallies, Uzor Kalu tells APC leaders"),
      "ngpolitics")
check("nd_1409_referee",
      filters.topic_block("“The Best Referee on the Continent”: Omar Artan’s Composed "
                          "Performance in a Tense Qualifier"),
      "sport")

# ── 2. 아프리카 분쟁 보도는 그대로 통과해야 한다 (남용 방지) ─────────────
check("keep_niger_ambush",
      filters.topic_block("Gunmen Kill at Least 38 in Ambush on Market Convoy in Western Niger"),
      None)
check("keep_boko_haram",
      filters.topic_block("Boko Haram militants kill 12 soldiers in Borno state"),
      None)
check("keep_plateau_displaced",   # 'displaced' = 단단한 사건 어휘 → 통과
      filters.topic_block("Tinubu promises to rebuild Plateau communities, return displaced persons"),
      None)

# ── 3. 다른 나라 정치·발언 기사는 막지 않는다 ────────────────────────────
check("keep_french_election",
      filters.topic_block("French presidential election: The unexpected buzz around "
                          "Cannes Mayor David Lisnard"),
      None)
check("keep_somaliland_slams",
      filters.topic_block("Somaliland slams Somalia over missing delegation in Kosovo"),
      None)
check("keep_china_denies",
      filters.topic_block("Nvidia in turmoil as China denies spying claims"),
      None)

# ── 4. 남은 구멍(알고 있는 것): 가정형 선거 전망은 이 규칙이 못 잡는다 ────
# 취향 축 '선거·관측'(-0.8)이 점수로만 눌러 준다. 제목 규칙으로 넓히면
# 기술·분석 기사('Can DeepSeek challenge OpenAI…')까지 걸려 별도 결정이 필요하다.
check("known_gap_hypothetical",
      filters.topic_block("Nigeria 2027: If Atiku is knocked out, does Tinubu or Obi gain?"),
      None)

print()
if FAIL:
    print(f"실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("전부 통과")
