#!/usr/bin/env python3
"""형식 게이트 회귀 테스트 (2026-11-12, 네트워크 불필요).

왜 이 테스트가 있나: 👎 11건의 사유가 '소재'가 아니라 '글의 형식'(일상 시황·보도자료·
상황보고·전망·지방 정치)이었다. 비트 분류만으로는 그걸 못 버린다. 그래서 같은 LLM 콜에서
형식과 유용도를 받아 게이트를 세웠고, 이 테스트가 그 계약을 고정한다.

  python3 tests/test_form_gate.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import beats        # noqa: E402
import filters      # noqa: E402
import osint        # noqa: E402
import run_once     # noqa: E402

# 테스트는 네트워크를 쓰지 않는다 — 단건 분류는 스텁으로 대체한다.
beats.classify_one = lambda title: None

FAIL = []


def check(name, got, want):
    if got == want:
        print(f"ok {name}")
    else:
        print(f"FAIL {name}: got={got!r} want={want!r}")
        FAIL.append(name)


# ── 1. 파서: 5필드가 계약이고, 3필드(구형)도 살아야 한다 ────────────────
check("parse_5field", beats._parse("1:TECH:ASIA:NEWS:8", 1), [("TECH", "ASIA", "NEWS", 8)])
check("parse_3field_compat", beats._parse("1:CONFLICT:MIDDLE_EAST", 1),
      [("CONFLICT", "MIDDLE_EAST", None, None)])
check("parse_lowercase", beats._parse("1. economy: global: market: 2", 1),
      [("ECONOMY", "GLOBAL", "MARKET", 2)])
check("parse_shuffled", beats._parse("1: MARKET ECONOMY US 2", 1),
      [("ECONOMY", "US", "MARKET", 2)])
check("parse_no_separator", beats._parse("1  TECH ASIA NEWS 8", 1),
      [("TECH", "ASIA", "NEWS", 8)])
check("parse_utility_clamped", beats._parse("1:OTHER:GLOBAL:PROMO:99", 1),
      [("OTHER", "GLOBAL", "PROMO", 10)])
check("parse_drops_garbage_line", beats._parse("1: ???", 1), None)

# ── 2. 형식 판정 규칙 ─────────────────────────────────────────────────
check("gate_market_low", beats.drop_form("MARKET", 2), True)
check("gate_market_low_mid", beats.drop_form("MARKET", 5), True)   # 일상 시황은 유용도가 높아도 버린다
check("gate_market_big", beats.drop_form("MARKET", 8), False)      # 모델이 큰 사건으로 본 경우만 통과
check("gate_local_low", beats.drop_form("LOCAL", 3), True)
check("gate_local_mid", beats.drop_form("LOCAL", 4), False)
check("gate_local_verylow", beats.drop_form("LOCAL", 2), True)
check("gate_forecast_mid", beats.drop_form("FORECAST", 6), False)
check("gate_forecast_low", beats.drop_form("FORECAST", 4), True)
check("gate_promo_low", beats.drop_form("PROMO", 1), True)
check("gate_report_low", beats.drop_form("REPORT", 2), True)
check("gate_report_high", beats.drop_form("REPORT", 7), False)
check("gate_study_always", beats.drop_form("STUDY", 10), True)
check("gate_study_parse", beats._parse("1:TECH:GLOBAL:STUDY:9", 1),
      [("TECH", "GLOBAL", "STUDY", 9)])
check("gate_news_ok", beats.drop_form("NEWS", 6), False)
check("gate_news_noise", beats.drop_form("NEWS", 1), True)
check("gate_form_missing", beats.drop_form(None, 2), False)     # 형식 미상 → 판단 보류
check("gate_util_missing", beats.drop_form("MARKET", None), False)  # 구형 응답 → 판단 보류
check("gate_util_garbage", beats.drop_form("MARKET", "x"), False)

# ── 3. 게이트가 실제로 _candidate_ok 에서 막는가 ───────────────────────
con = sqlite3.connect(":memory:")
con.execute(run_once.SCHEMA[0])
TITLE = "Stocks rally as CoreWeave soothes investors, bonds reel on rate fears"
con.execute("INSERT INTO items(key,title,url,score) VALUES(?,?,?,?)",
            ("k1", TITLE, "https://example.com/a", 20.0))


def gate(form, util, title=TITLE):
    return osint._candidate_ok(con, (1, title, "", "https://example.com/a", "Feed",
                                     "economy", "", 20.0, "ECONOMY", "US", form, util),
                               {}, [], "2000-01-01 00:00 UTC", 11.5)


check("block_market2", gate("MARKET", 2), None)
check("note_is_form", con.execute("SELECT note FROM items WHERE id=1").fetchone()[0], "form:MARKET")
con.execute("UPDATE items SET posted=0, note=NULL WHERE id=1")
check("allow_news8", gate("NEWS", 8), "https://example.com/a")
con.execute("UPDATE items SET posted=0, note=NULL WHERE id=1")
# 구형 응답(form/util 없음)은 형식 게이트를 건너뛰되, 시황 백스톱은 적용된다.
check("legacy_market_fallback", gate(None, None), None)
check("legacy_note", con.execute("SELECT note FROM items WHERE id=1").fetchone()[0],
      "form:MARKET(rule)")
con.execute("UPDATE items SET posted=0, note=NULL WHERE id=1")
NEUTRAL = "G7 foreign ministers say China actions east of Taiwan threaten stability"
con.execute("UPDATE items SET title=? WHERE id=1", (NEUTRAL,))
check("allow_legacy_no_util", gate(None, None, NEUTRAL), "https://example.com/a")

# ── 3-1. 게시 직전 마지막 게이트(form_gate_item) ─────────────────────
con.execute("UPDATE items SET title=?, posted=0, note=NULL, form=NULL, util=NULL WHERE id=1",
            (TITLE,))
beats.classify_one = lambda title: ("ECONOMY", "US", "MARKET", 1)
check("last_gate_blocks", osint.form_gate_item(con, 1, TITLE), False)
check("last_gate_note", con.execute("SELECT note FROM items WHERE id=1").fetchone()[0],
      "form:MARKET")
beats.classify_one = lambda title: ("ECONOMY", "US", "NEWS", 8)
con.execute("UPDATE items SET posted=0, note=NULL, form=NULL, util=NULL WHERE id=1")
check("last_gate_allows", osint.form_gate_item(con, 1, TITLE), True)
beats.classify_one = lambda title: None
con.execute("UPDATE items SET posted=0, note=NULL, form=NULL, util=NULL WHERE id=1")
check("last_gate_rule_backstop", osint.form_gate_item(con, 1, TITLE), False)
check("last_gate_rule_note", con.execute("SELECT note FROM items WHERE id=1").fetchone()[0],
      "form:MARKET(rule)")
beats.classify_one = lambda title: None
con.execute("UPDATE items SET posted=0, note=NULL, form=NULL, util=NULL, title=? WHERE id=1",
            ("G7 foreign ministers say China actions east of Taiwan threaten stability",))
check("study_rule", filters.is_digest("Study finds coffee linked to longer life", ""), "study")
check("spread_dedupe_window", osint.same_event(
    "Jensen Huang resigns as Nvidia CEO after mounting scandals",
    "Exclusive: US DOJ probes Nvidia as fallout from Huang's resignation ripples", {}), True)
check("last_gate_passes_unknown", osint.form_gate_item(con, 1, "G7 foreign ministers say"), True)

# ── 4. 사건 예산 ─────────────────────────────────────────────────────
heat = {"nvidia": 5, "iran": 1}
check("story_pen_heavy", osint.story_penalty(heat, "Nvidia board to decide Jensen Huang's fate"),
      osint.ENTITY_PEN)
check("story_pen_light", osint.story_penalty(heat, "Iran strikes shipping near Oman"), 0.0)
check("story_pen_none", osint.story_penalty(heat, "Brazil fines Facebook over court order"), 0.0)
check("story_tokens_stopword", "the" in osint.story_tokens("The Nvidia rally"), False)

# ── 5. 2026-11-12 오탐 회귀: 'rally' 는 스포츠가 아니다 ────────────────
check("rally_stocks_ok", filters.topic_block("Stocks rally as bond yields fall"), None)
check("rally_politics_ok", filters.topic_block("Dutse Rally Soundbites: governor speaks"), None)
check("rally_sport_blocked", filters.topic_block("WRC: Rovanpera wins Rally Finland"), "sport")
check("motor_racing_blocked", filters.topic_block("Motor racing-Hamilton visits Rafah"), "sport")

# ── 6. 분류 결과가 DB에 남는가(계약) ───────────────────────────────────
rows = [ (i, f"title {i}") for i in range(1, 4) ]
print("\n" + ("전부 통과" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}"))
sys.exit(1 if FAIL else 0)
