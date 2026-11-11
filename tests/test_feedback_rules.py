#!/usr/bin/env python3
"""규칙 회귀 테스트 (네트워크 불필요).  python3 tests/test_feedback_rules.py

1) 2026-11-10 👎 #737(FormatBiz) 계기로 추가한 '잘린 링크' 규칙
2) 2026-11-10 👎 표본에서 나온 라이브블로그 규칙
3) taste.py 취향 축 — 👎 표본(나이지리아 내수·행사 홍보)이 밀리고, 👍 표본(프랑스·이란
   사이버 대응)이 밀리지 않는지, 차단은 축이 겹칠 때만 일어나는지
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import filters                                                  # noqa: E402
import osint                                                    # noqa: E402
import taste                                                    # noqa: E402

T = "Content Americas 2027 Expands into Kids Entertainment with New Summit"


def test_truncated_url():
    assert osint.junk_item(T, "https://www.formatbiz.it/dettNews.aspx?id") is True
    assert osint.junk_item(T, "https://x.test/a?pid=") is True


def test_normal_url_kept():
    assert osint.junk_item(T, "https://www.formatbiz.it/dettNews.aspx?id=12345&lang=en") is False
    assert osint.junk_item(
        "Nigeria turns to hybrid cloud infrastructure for its postcode database",
        "https://techcabal.com/2026/11/10/nigeria-hybrid-cloud-storage-postcode/") is False


def test_live_blog_is_digest():
    assert filters.is_digest(
        "Cop31 live: slight drop in fossil fuel emissions revealed as conference enters second day",
        "https://www.theguardian.com/environment/live/2026/nov/10/cop31") == "digest"


def test_taste_prefers_event_news():
    good = taste.adj("France vows tough response to freezer cyberattack blamed on Iran")
    bad = taste.adj("Nigeria 2027: If Atiku is knocked out, does Tinubu or Obi gain?")
    assert good > 0, good
    assert bad < good, (good, bad)
    assert taste.LIMIT_LO <= bad <= taste.LIMIT_HI


def test_taste_block_needs_two_axes():
    # 한 축만 걸린 글은 차단되지 않는다
    assert taste.blocked("France vows tough response to cyberattack blamed on Iran") is False
    # 내수 정치 + 행사 홍보가 겹치면 차단
    assert taste.blocked("Nigeria summit: Dangote unveils refinery pavilion at Lagos expo") is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("전부 통과")

# ── 2026-11-10 👎 11건 사유 분석 → 주제 차단 규칙 ───────────────────────────
def test_topic_block_from_dislikes():
    # 👎 #950 잠비아 U23 축구 / #942 교회 30주년
    assert filters.topic_block("Zambia U23 arrive in Ouagadougou for AFCON qualifier") == "sport"
    assert filters.topic_block("Dunamis Church celebrates 30th anniversary") == "life"
    # 👎 #769 장학금 수여 / #737 행사 확장
    assert filters.topic_block("Airtel Africa Foundation Awards Scholarships to 10 Young Women in Niger") == "promo"
    assert filters.topic_block("Content Americas 2027 Expands into Kids Entertainment with New Pavilion") == "promo"

def test_topic_keeps_real_news():
    for t in ("Parliament passes budget bill after marathon session",        # 'marathon' 오탐 금지
              "Nvidia unveils new AI chip at GTC conference",                # 'conference' 오탐 금지
              "Gaza strike kills 20 as ceasefire talks stall",
              "Qatar PM joins G7 talks on Iran after Trump meeting"):
        assert filters.topic_block(t) is None, t

def test_korean_titles_allowed():
    # GeekNews(한국어) — 영문 3자가 없다고 버리면 안 된다
    assert osint.junk_item("에이전트가 글을 제대로 쓰도록 만들기", "https://news.hada.io/topic?id=1") is False
