#!/usr/bin/env python3
"""writer.py — 기사에서 '정보 밀도 높은 문장'만 골라 정리형 본문을 만든다.

핵심: 문단을 통째로 번역하지 않는다.
  1) 본문 문단 → 문장 단위 분해
  2) 잡음(내비·저자소개·광고) 제거
  3) 문장별 점수: 숫자/금액/퍼센트 + 인용 + 고유명사 + 제목 키워드 겹침
  4) 리드 문장 1개 + 고득점 문장 2~3개를 원문 순서대로 이어 붙임
  5) 번역 → 중복 문장 제거 → 해요체 변환
"""
import argparse, html, re, sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

JUNK_PHRASES = [
    "news article searches", "this will let", "google show", "enable javascript",
    "outdated browser", "click here", "sign in", "log in", "you are using",
    "please use", "image caption", "copy link", "add al jazeera", "add to google",
    "whatsapp", "facebook", "twitter", "share this", "published on", "updated on",
    "getty images", "file photo", "advertisement", "supported by", "newsletter",
]
BOILER = [
    "cookie", "subscribe", "sign up", "all rights reserved", "follow us",
    "download the app", "read more:", "related:", "copyright", "terms of service",
    "privacy policy", "무단전재",
]
BIO = re.compile(
    r"(?i)(\bi (have been|am a|cover|write|report)\b|has been covering|"
    r"correspondent|senior (reporter|editor)|award-winning|\bjoined\b.{0,30}\b(19|20)\d\d\b|"
    r"follow (him|her|me|us) on|@[a-z0-9.]+\.(com|net|org)|based in [A-Z][a-z]+|"
    r"writes? (about|for)|\bI (covered|interviewed|traveled)\b)")
VERB = re.compile(r"\b(is|are|was|were|has|have|had|will|would|said|says|told|reported|"
                  r"announced|signed|agreed|killed|hit|rose|fell|warned|called|met|"
                  r"approved|launched|elected|resigned|attacked|struck|banned|imposed|"
                  r"urged|vowed|expects|plans|seeks|faces|wins|loses|reaches)\b", re.I)
NUM = re.compile(r"(\d[\d.,]*\s?(%|percent|billion|million|trillion|bn|mn)?|\$\d|€\d|£\d)")
ABBR = [("U.S.", "US"), ("U.K.", "UK"), ("Mr.", "Mr"), ("Ms.", "Ms"),
        ("Dr.", "Dr"), ("Inc.", "Inc"), ("Ltd.", "Ltd"), ("Co.", "Co"),
        ("St.", "St"), ("No.", "No"), ("vs.", "vs")]


def fetch_html(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def resolve_google(url, timeout=20):
    """news.google.com/rss/articles/... → 실제 기사 URL."""
    import json, urllib.parse
    if "news.google.com" not in url:
        return url
    try:
        aid = url.split("/articles/")[1].split("?")[0]
        req = urllib.request.Request(f"https://news.google.com/rss/articles/{aid}",
                                     headers={"User-Agent": UA})
        page = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        sg = re.search(r'data-n-a-sg="([^"]+)"', page)
        ts = re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return url
        inner = json.dumps(["garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
              None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1,
             None, 0, 0, None, 0], aid, ts.group(1), sg.group(1)])
        payload = json.dumps([[["Fbv4je", inner]]])
        data = urllib.parse.urlencode({"f.req": payload}).encode()
        req2 = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute", data=data,
            headers={"User-Agent": UA,
                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        txt = urllib.request.urlopen(req2, timeout=timeout).read().decode("utf-8", "replace")
        body = txt.split("\n\n")[1] if "\n\n" in txt else txt
        m = re.search(r'(https?://[^"\\]+)', body)
        return m.group(1) if m else url
    except Exception:
        return url


def main_zone(page: str) -> str:
    m = re.search(r"(?is)<article[^>]*>(.*?)</article>", page)
    if m and len(m.group(1)) > 800:
        return m.group(1)
    m = re.search(r'(?is)<div[^>]+(?:itemprop=["\']articleBody|class=["\'][^"\']*(?:article-body|story-body|post-content))[^>]*>(.*?)</div>', page)
    if m and len(m.group(1)) > 800:
        return m.group(1)
    return page


def jsonld_body(page: str):
    """구조화 데이터(JSON-LD)의 articleBody — 사이트가 공개한 정식 본문."""
    import json
    for m in re.finditer(r'"articleBody"\s*:\s*"(.*?)"(?=\s*,\s*")', page, re.S):
        raw = m.group(1)
        try:
            txt = json.loads('"' + raw + '"')
        except Exception:
            continue
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) > 300:
            return txt
    return ""


def paragraphs(page: str):
    """<p> 가 없는 사이트도 있으므로, 태그를 걷어낸 텍스트 블록에서 문단을 만든다."""
    jb = jsonld_body(page)
    if jb:
        return [jb]
    page = main_zone(page)
    page = re.sub(r"(?is)<(script|style|noscript|svg|form|nav|header|footer|aside)[^>]*>.*?</\1>", " ", page)
    # 블록 경계를 개행으로
    page = re.sub(r"(?i)</(p|div|section|article|li|h[1-6]|blockquote)>", "\n", page)
    page = re.sub(r"(?i)<br\s*/?>", "\n", page)
    text = re.sub(r"(?is)<[^>]+>", " ", page)
    text = html.unescape(text)
    out, seen = [], set()
    for line in text.split("\n"):
        t = re.sub(r"\s+", " ", line).strip()
        if len(t) < 50:
            continue
        low = t.lower()
        if any(b in low for b in BOILER) or any(j in low for j in JUNK_PHRASES):
            continue
        if BIO.search(t) or "..." in t or "…" in t:
            continue
        if re.match(r"(?i)^(by|written by|words by|image|video|photo|advertisement)\b", t):
            continue
        if not VERB.search(t):
            continue
        letters = sum(c.isascii() and c.isalpha() for c in t)
        if letters / max(len(t), 1) < 0.72:
            continue
        words = t.split()
        caps = sum(1 for w in words if w[:1].isupper())
        if len(words) > 6 and caps / len(words) > 0.62:
            continue
        if t.count("|") >= 2 or t.count("»") >= 2 or t.count("→") >= 2:
            continue
        k = t[:60].lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def og_desc(page: str):
    m = re.search(r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']', page)
    if not m:
        m = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', page)
    return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""


def sentences(paras):
    out = []
    for p in paras:
        s = p
        for a, b in ABBR:
            s = s.replace(a, b)
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“0-9])", s)
        for part in parts:
            part = part.strip()
            if len(part) < 30:
                continue
            out.append(part)
    return out


STOP = set("the a an of to in on for and as at by with from says say new this that "
           "its his her their was were been has have had not but".split())


def toks(t):
    return set(w for w in re.findall(r"[a-zA-Z]{3,}", t.lower()) if w not in STOP)


def score_sent(sent, title_tokens, idx=0):
    pts = 0.0
    pts += max(0.0, 3.0 - 0.25 * idx)        # 앞쪽 문장 우대
    if idx == 0:
        pts += 1.5
    if len(re.findall(r"\d", sent)) >= 4:
        pts -= 1.0                            # 통계 나열 문장 억제
    if re.search(r"(?i)\b(president|administration|officials?|minister|spokes|"
                 r"government|military|secretary|prime minister)\b", sent):
        pts += 1.2
    if NUM.search(sent):
        pts += 3
    if "%" in sent or "percent" in sent.lower():
        pts += 1
    if '"' in sent or "“" in sent:
        pts += 1.5
    overlap = len(toks(sent) & title_tokens)
    pts += min(overlap, 4) * 1.2
    if re.search(r"(?i)\b(said|told|announced|warned|according to|stated|declared)\b", sent):
        pts += 1
    if re.search(r"(?i)\b(will|would|expects|plans|may|could)\b", sent):
        pts += 0.5
    if re.search(r"(?i)\b(after|as |because|following|amid|due to|sparked|fueled|"
                 r"prompted|drove)\b", sent):
        pts += 2.0                            # 원인·계기 문장 우대
    if len(MONEY.findall(sent)) >= 2 and not re.search(
            r"(?i)\b(president|administration|officials?|minister|democrat|republican|"
            r"opec|government|military|election)\b", sent):
        pts -= 1.5                            # 가격 나열만 하는 문장 억제
    n = len(sent)
    if n < 60:
        pts -= 1.5
    elif n > 320:
        pts -= 1.5
    return pts


def pick_sentences(paras, title, want=2, avoid=""):
    sents = sentences(paras)
    if not sents:
        return []
    tt = toks(title)
    at = toks(avoid) if avoid else set()
    scored = []
    for i, s2 in enumerate(sents):
        sc = score_sent(s2, tt, i)
        if at:
            st = toks(s2)
            if st:
                sc -= 2.0 * len(st & at) / max(len(st | at), 1)   # 리드와 겹치면 감점
        scored.append((sc, i, s2))
    lead = sents[0]
    chosen = {0}
    money_seen = set(MONEY.findall(sents[0]))
    for sc, i, s in sorted(scored[1:], key=lambda x: -x[0]):
        if len(chosen) >= want + 1:
            break
        if any(len(toks(s) & toks(sents[j])) / max(len(toks(s) | toks(sents[j])), 1) > 0.5
               for j in chosen):
            continue
        nums = set(MONEY.findall(s))
        if nums & money_seen:            # 같은 가격을 반복하는 문장은 버린다
            continue
        money_seen |= nums
        chosen.add(i)
    return [sents[i] for i in sorted(chosen)]


def build(paras, og, translate, title="", want=2):
    if not paras:
        return translate(og) if og else ""
    lead = og.strip() if og and len(og) >= 70 else ""
    picks = pick_sentences(paras, title, want + (1 if lead else 0), avoid=lead)
    if lead:
        # 사이트가 제공한 요약문을 리드로 쓰고, 겹치지 않는 문장만 덧붙인다
        lt = toks(lead)
        picks = [s for s in picks
                 if len(toks(s) & lt) / max(len(toks(s) | lt), 1) < 0.35][:want]
        parts = [lead] + picks
    else:
        parts = picks
    return " ".join(translate(x) for x in parts).strip()


GLOSSARY = [
    ("중간고사", "중간선거"), ("중간 결과", "중간선거 결과"), ("중간 선거", "중간선거"),
    ("이란 팬이", "이란 편이"), ("인공지능", "AI"), ("유럽 연합", "EU"),
]

MONEY = re.compile(r"[$€£]\s?\d[\d.,]*")

ENDINGS = [
    (r"했습니다(?=[\s.,]|$)", "했어요"), (r"됐습니다(?=[\s.,]|$)", "됐어요"),
    (r"있습니다(?=[\s.,]|$)", "있어요"), (r"없습니다(?=[\s.,]|$)", "없어요"),
    (r"합니다(?=[\s.,]|$)", "해요"), (r"입니다(?=[\s.,]|$)", "이에요"),
    (r"([가-힣]{1,5})했다(?=[\s.]|$)", r"\1했어요"),
    (r"([가-힣]{1,5})됐다(?=[\s.]|$)", r"\1됐어요"),
    (r"([가-힣]{1,5})밝혔다(?=[\s.]|$)", r"\1밝혔어요"),
    (r"([가-힣]{1,5})전했다(?=[\s.]|$)", r"\1전했어요"),
    (r"([가-힣]{1,5})말했다(?=[\s.]|$)", r"\1말했어요"),
    (r"([가-힣]{1,5})강조했다(?=[\s.]|$)", r"\1강조했어요"),
]


def _vowel(ch):
    code = ord(ch) - 0xAC00
    if code < 0 or code > 11171:
        return None
    return (code // 28) % 21


def _stem_ayo(stem: str) -> str:
    if stem.endswith("하"):
        return stem[:-1] + "해요"
    v = _vowel(stem[-1]) if stem else None
    return stem + ("아요" if v in (0, 8) else "어요")


def to_haeyo(s: str) -> str:
    s = re.sub(r"([가-힣]{1,6})합니다(?=[\s.,]|$)", r"\1해요", s)
    s = re.sub(r"([가-힣]{1,6})됩니다(?=[\s.,]|$)", r"\1돼요", s)
    s = re.sub(r"([가-힣]{1,6})입니다(?=[\s.,]|$)", r"\1이에요", s)
    for pat, rep in ENDINGS:
        s = re.sub(pat, rep, s)
    s = re.sub(r"([가-힣]{2,})습니다(?=[\s.,]|$)", lambda m: _stem_ayo(m.group(1)), s)
    for pat, rep in ((r"고 있다(?=[\s.,]|$)", "고 있어요"),
                     (r"고 있었다(?=[\s.,]|$)", "고 있었어요"),
                     (r"수 있다(?=[\s.,]|$)", "수 있어요"),
                     (r"것이다(?=[\s.,]|$)", "거예요"),
                     (r"([가-힣]{1,4})한다(?=[\s.]|$)", r"\1해요"),
                     (r"([가-힣]{1,4})된다(?=[\s.]|$)", r"\1돼요"),
                     (r"([가-힣]+)았다(?=[\s.]|$)", r"\1았어요"),
                     (r"([가-힣]+)었다(?=[\s.]|$)", r"\1었어요"),
                     (r"([가-힣]+)였다(?=[\s.]|$)", r"\1였어요"),
                     (r"([가-힣]+)졌다(?=[\s.]|$)", r"\1졌어요"),
                     (r"(?<![가-힣])있다(?=[\s.]|$)", "있어요"),
                     (r"(?<![가-힣])없다(?=[\s.]|$)", "없어요"),
                     (r"이다(?=[\s.]|$)", "이에요")):
        s = re.sub(pat, rep, s)
    for a, b in (("줍니다", "줘요"), ("봅니다", "봐요"), ("갑니다", "가요"),
                 ("옵니다", "와요"), ("십니다", "세요"), ("힙니다", "혀요"),
                 ("듭니다", "들어요"), ("밝혔다", "밝혔어요"), ("전했다", "전했어요")):
        s = s.replace(a, b)
    return s


def drop_repeats(s: str, thresh=0.55) -> str:
    sents = re.split(r"(?<=[.!?])\s+", s.strip())
    keep, seen = [], []
    for sent in sents:
        w = set(re.findall(r"[가-힣]{2,}", sent))
        if not w:
            keep.append(sent)
            continue
        if any(len(w & p) / max(len(w | p), 1) > thresh for p in seen):
            continue
        keep.append(sent)
        seen.append(w)
    return " ".join(keep)


def korean_smooth(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(그런데|또한|그러나),?\s*", "", s)
    s = s.replace(" .", ".").replace(" ,", ",")
    s = drop_repeats(s)
    s = to_haeyo(s)
    for a, b in GLOSSARY:
        s = s.replace(a, b)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    sys.path.insert(0, "/var/minis/shared/osint")
    import osint
    page = fetch_html(a.url)
    ps = paragraphs(page)
    sents = sentences(ps)
    print(f"[debug] paras={len(ps)} sents={len(sents)}", file=sys.stderr)
    for s in pick_sentences(ps, a.title or (sents[0] if sents else "")):
        print("  *", s[:100], file=sys.stderr)
    body = build(ps, og_desc(page), osint.translate, a.title)
    print("\n" + (korean_smooth(body) if body else "(none)"))


if __name__ == "__main__":
    main()
