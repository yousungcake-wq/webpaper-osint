#!/usr/bin/env python3
"""filters.py — 게시 직전 품질·중복 게이트 (2026-11-09)

세 가지를 담당한다. 전부 실제 사고 기록에서 나왔다.

1) 라운드업·오피니언·종목추천 판별
   한 페이지에 여러 사건을 섞은 '라운드업' 글은 요약이 뒤섞이고
   (금리 기사에 F1 우승이 붙는 식), 다음 날 같은 사실이 다른 URL로 또 올라온다.
   2026-11-09 실측: Rio Times 'The Big Read Brazil' 가 06:50·07:30 두 번 게시됨.
   오피니언(…, By 홍길동)과 종목 추천도 뉴스가 아니라서 뺀다.

2) URL 정규화
   같은 기사의 ?utm=, /amp, 끝 슬래시 차이는 같은 주소다. 기존 가드는
   문자열 완전일치라 같은 글을 다른 주소로 다시 받으면 통과했다.

3) 요약문 유사도 (교차 매체 중복)
   영문 제목이 달라도 같은 사건이면 한국어 요약이 거의 같아진다.
   실측: 채널 100건 4,950쌍 중 p99.5 = 0.117, 최댓값 0.444(실제 중복 쌍).
   임계값 0.25 는 오탐이 사실상 0 인 지점이다.
"""
import re
from urllib.parse import urlsplit

# 한 페이지가 여러 사건을 담는 글 (라운드업·브리핑·속보 블로그)
ROUNDUP_TITLE = re.compile(
    r"(?i)("
    r"daily brief|the big read|morning brief|evening brief|midday brief|"
    r"what to know|things to know|as it happened|live blog|live updates?|"
    r"latest updates|markets? wrap|market wrap|closing bell|opening bell|"
    r"\broundup\b|\brecap\b|week in review|weekly wrap|top \d+ (stories|news|things)|"
    r"morning digest|news digest|newsletter|first thing|the brief\b"
    r")")

# 라운드업으로 보이는 URL 조각
ROUNDUP_URL = re.compile(
    r"(?i)/(daily-brief|the-big-read|live-blog|liveblog|live-updates?|roundup|"
    r"morning-brief|markets-wrap|what-to-know)(/|-|$)")

# 오피니언·사설
OPINION_URL = re.compile(
    r"(?i)/(opinion|opinions|commentary|voices|editorial|editorials|columns?|op-?ed)(/|$)")
OPINION_TITLE = re.compile(
    r"(?i)^(opinion|commentary|editorial|column|analysis)\s*[:\-]|"
    r",\s*by\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?$|"
    r"^(why|what|how)\s.+\?$")

# 종목 추천·시황 전망 (투자 광고)
STOCK_PICK = re.compile(
    r"(?i)(stock picks?|stocks to buy|top picks?|best stocks|"
    r"investment ideas|buy list|market outlook|worth (buying|watching))")


def is_digest(title, url):
    """라운드업/오피니언/종목추천이면 이유 문자열, 아니면 None."""
    t = title or ""
    u = url or ""
    if ROUNDUP_TITLE.search(t) or ROUNDUP_URL.search(u):
        return "digest"
    if OPINION_URL.search(u):
        return "opinion"
    if STOCK_PICK.search(t):
        return "stockpick"
    if OPINION_TITLE.search(t) and len(t) < 220:
        return "opinion"
    return None


def canon_url(url):
    """같은 기사를 가리키는 주소 차이를 지운다.

    https://www.Example.com/a/b/?utm_source=x#frag  →  example.com/a/b
    """
    if not url:
        return ""
    try:
        p = urlsplit(url.strip())
    except Exception:
        return url.strip().lower()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("amp."):
        host = host[4:]
    path = re.sub(r"/amp/?$", "/", p.path)
    path = re.sub(r"/amp\.html?$", "/", path)
    path = re.sub(r"/index\.html?$", "/", path)
    path = path.rstrip("/")
    return f"{host}{path}".lower()


def _grams(s, n=3):
    s = re.sub(r"[^가-힣0-9A-Za-z ]", " ", (s or ""))
    s = re.sub(r"\s+", " ", s).strip().lower()
    if len(s) < n:
        return set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def overlap(a, b, n=3):
    """두 한국어 요약문의 문자 n-gram 자카드 유사도 (0~1)."""
    ga, gb = _grams(a, n), _grams(b, n)
    if len(ga) < 10 or len(gb) < 10:
        return 0.0
    return len(ga & gb) / len(ga | gb)
