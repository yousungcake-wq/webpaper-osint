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
    r"\blive\s*[:\u2013-]|"                     # "Cop31 live: …" (2026-11-10 👎 #741)
    r"latest updates|markets? wrap|market wrap|closing bell|opening bell|"
    r"\broundup\b|\brecap\b|week in review|weekly wrap|top \d+ (stories|news|things)|"
    r"morning digest|news digest|newsletter|first thing|the brief\b"
    r")")

# 라운드업으로 보이는 URL 조각
ROUNDUP_URL = re.compile(
    r"(?i)/(daily-brief|the-big-read|live-blog|liveblog|live-updates?|roundup|"
    r"morning-brief|markets-wrap|what-to-know|live)(/|-|$)")

# 오피니언·사설
OPINION_URL = re.compile(
    r"(?i)/(opinion|opinions|commentary|voices|editorial|editorials|columns?|op-?ed)(/|$)")
OPINION_TITLE = re.compile(
    r"(?i)^(opinion|commentary|editorial|column|analysis)\s*[:\-]|"
    r",\s*by\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?$|"
    r"^(why|what|how)\s.+\?$")

# 종목 추천·시황 전망 (투자 광고)
# 오피니언·에세이 (2026-11-12: "A Rothbardian Critique of US Intervention" 가 NEWS 로 통과했다)
ESSAY = re.compile(
    r"(?i)(\bop-?ed\b|\bcommentary\b|\bcritique\b|\bessay\b|\bcolumn\b|"
    r"\bviewpoint\b|\bperspective\b|\bthe big picture\b|\bpodcast\b|\btranscript\b)")

# 뉴스가 아닌 글 — 조언 칼럼·영화 시상·시황 요약·미리보기 목록 (2026-11-13 실측 4/4 정확)
NONNEWS = re.compile(
    r"(?ix)("
    r"\b\d+\ things\ to\ (watch|know)|call\ sheet|\#video_?news|"
    r"(wins?|won|takes?)\b.{0,30}\b(awards?|prizes?|medals?)\b|"
    r"\b(oscars?|golden\ rooster|golden\ globe|film\ festival)\b|"
    r"^(my|our)\ (wife|husband|partner|family)\b|should\ (we|i|you)\b|"
    r"\bpay\ off\b.{0,30}\bmortgage\b|here'?s\ how\ much|what\ to\ do\ if"
    r")")

STOCK_PICK = re.compile(
    r"(?i)(stock picks?|stocks to buy|top picks?|best stocks|"
    r"investment ideas|buy list|market outlook|worth (buying|watching))")


# ── 주제 차단 (2026-11-10 👎 분석) ────────────────────────────────────────
# 👎 11건의 사유를 세어 보니 3덩어리였다.
#   아프리카 비분쟁(4) · 스포츠/종교/생활(2: 잠비아 U23 축구, 교회 30주년)
#   · 홍보/의례(3: 장학금 수여, 대사 학교 방문, 행사 확장)
# 여기서는 국제 뉴스로서 정보 가치가 없는 **주제**를 먼저 걸러낸다.
SPORT = re.compile(
    r"(?i)\b(u-?1[5-9]|u-?2[0-3]|under-?\d{2}|national team|national squad|"
    r"world cup|premier league|la liga|serie a|bundesliga|champions league|europa league|"
    r"afcon|africa cup of nations|copa|nba|nfl|mlb|nhl|cricket|olympic|olympics|"
    r"athletics|grand prix|formula one|motor racing|nascar|indycar|motogp|"
    r"wrc|rally championship|rallycross|"      # 2026-11-12: 맨 'rally' 는 stocks rally·정치 집회를 잡는다
    r"tennis|golf|boxing|"
    r"fifa|caf|uefa|ioc)\b")

RELIGION_LIFE = re.compile(
    r"(?i)\b(church|cathedral|pastor|bishop|diocese|congregation|ministry\b(?! of)|"
    r"mosque|imam|rabbi|temple|anniversary|celebrates? \d+|wedding|funeral|"
    r"beauty pageant|miss world|reality show|box office|celebrity gossi?p)\b")

CSR_PROMO = re.compile(
    r"(?i)(\b(scholarships?|donat(e|es|ed|ion)|charit\w+|foundation (awards?|grants?)|"
    r"ribbon.?cutting|ground.?breaking)\b|"
    r"\b(expo|exhibition|pavilion|trade show|showcase|festival)\b.{0,45}"
    r"\b(expands?|debuts?|returns?|line.?up|edition)\b|"
    r"\b(expands?|debuts?)\b.{0,45}\b(expo|pavilion|trade show|showcase)\b)")

# ── 일상 시황 백스톱 (2026-11-12) ──────────────────────────────────────
# 형식 판정은 분류 LLM 콜에 실려 있다. 그 콜이 429/503 이면 form 이 비어 게이트가
# 꺼지는데, 무료 키는 하루에도 몇 번씩 그렇게 된다. 그때만 쓰는 **좁고 정확한** 규칙이다.
# 2026-11-12 실측(게시 212건): 17/17 정밀도(오탐 0), 재현율 63%.
# 평상시 경로가 아니다 — form 이 채워져 있으면 이 함수는 호출되지 않는다.
_MKT_VERB = (r"(rise|rises|rising|fall|falls|falling|drop|drops|gain|gains|climb|climbs|"
             r"slip|slips|jump|jumps|sink|sinks|rebound|rebounds|tumble|tumbles|extend|"
             r"extends|poised|set to|surge|surges|slide|slides|dip|dips|rally|rallies|"
             r"retreat|retreats|regain|regains|pick up|picks up|whipsaw|edge|edges|"
             r"mixed|flat|low|high|record)")
MARKET_WRAP = re.compile(
    r"(?ix)(stock\ market\ today|market\ (wrap|report|today)|earnings\ call|key\ facts|"
    r"(stocks?|shares?|index|futures|nasdaq|s&p|dow|yields?|bonds?|dollar|sterling|yen|"
    r"euro|crude)\b.{0,50}\b" + _MKT_VERB + r"|"
    + _MKT_VERB + r"\b.{0,30}\b(stocks|shares|index|futures|nasdaq|yield|dollar|wall street)|"
    r"bargain\ or\ a\ falling|target\ price|price\ target|market\ outlook|"
    r"stock\ picks|stocks\ to\ (buy|watch)|top\ gainers|top\ losers)")


def market_wrap(title):
    """일상 시황 기사인가 — LLM 형식 판정이 없을 때만 쓰는 백스톱."""
    return bool(MARKET_WRAP.search(title or ""))


def topic_block(title):
    """주제 자체가 뉴스 가치 없는 것(스포츠·종교·생활·홍보/의례)이면 이유 문자열."""
    t = title or ""
    if SPORT.search(t):
        return "sport"
    if RELIGION_LIFE.search(t):
        return "life"
    if CSR_PROMO.search(t):
        return "promo"
    return None

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
    if ESSAY.search(t):
        return "opinion"
    if NONNEWS.search(t):
        return "nonnews"
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
