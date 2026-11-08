#!/usr/bin/env python3
"""news_fetch.py — RSS/Atom/Google News 수집기 (2026-11-06 재작성)

osint.py 가 기대하는 API:
  load_sources(path) -> [(cat, name, url), ...]
  fetch(url, timeout=15) -> bytes
  parse_feed(data) -> [{"title","desc","url","ts","orig_source"}, ...]
  norm_title(t) -> str
  PRIORITY: {feed_name: score_bonus}
"""
import datetime as dt
import gzip
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36")

PRIORITY = {
    "Reuters Biz": 3, "Reuters World": 3, "Bloomberg MKTS": 3,
    "FT Markets": 3, "FT World": 3, "AP Top": 3, "AP World": 3,
    "TechCrunch": 3, "The Verge": 2, "Ars Technica": 2, "Wired": 2,
    "MIT TR": 2, "Register": 2, "CNBC Finance": 2, "MarketWatch": 2,
    "Politico EU": 2, "Guardian World": 1, "BBC World": 1, "AlJazeera": 1,
    "DW World": 1, "SCMP World": 1, "Nikkei Asia": 2, "Straits Times": 1,
    # 저보도 분쟁 지역 매체는 우대 (2026-11-07)
    "Radio Dabanga": 3, "Sudan War Monitor": 3, "HumAngle": 3,
    "AllAfrica": 2, "Africa Report": 2, "African Arguments": 2,
    "Premium Times": 2, "M&G Africa": 2, "Syria Direct": 2,
    "Middle East Eye": 2, "The New Arab": 2, "Al-Monitor": 2,
    "GN Sudan": 2, "GN Sahel": 2, "GN Congo": 2, "GN Horn": 2,
    "GN AfricaWar": 2, "GN SudanTrib": 2, "GN Yemen": 1, "GN Levant": 1,
    # 2026-11-08 신규 (검증: 피드 정상·최신)
    "Semafor": 2, "New Lines": 2, "Sahel Intelligence": 2,
    "Bellingcat": 1, "Rest of World": 1, "TechCabal": 1,
    # 구글뉴스 경유로 섞여 들어오는 잡음 매체 — 점수에서 깎는다.
    # (구글뉴스 항목은 feed 가 원 발행사 이름으로 바뀌어 여기 걸린다)
    "Latest news from Azerbaijan": -3, "파이낸셜뉴스": -3, "BBN Times": -3,
    "Cape Town ETC": -2, "24/7 Wall St.": -2, "teleSUR English": -2,
    "Mezha.net": -2, "Oz Arab Media": -3,
}


def load_sources(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            cat, name, url = parts[0].strip(), parts[1].strip(), "|".join(parts[2:]).strip()
            if url.startswith("http"):
                out.append((cat, name, url))
    return out


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
    return data


def norm_title(t):
    t = html.unescape(t or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _txt(el):
    return re.sub(r"\s+", " ", (el.text or "")).strip() if el is not None else ""


def _clean(s, limit=700):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _ts(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            d = dt.datetime.strptime(s.replace("Z", "+0000"), fmt.replace("Z", "%z")
                                     if "%z" in fmt else fmt)
            return d.astimezone(dt.timezone.utc) if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except Exception:
            continue
    return None


def _local(tag):
    return tag.split("}")[-1].lower()


def parse_feed(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "ignore")
    data = data.lstrip()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        root = ET.fromstring(data)
    except Exception:
        # 깨진 XML 보정: 잘못된 엔티티 제거 후 재시도
        fixed = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", data)
        root = ET.fromstring(fixed)

    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "desc": "", "url": "", "ts": None, "orig_source": ""}
        for ch in el:
            t = _local(ch.tag)
            if t == "title":
                d["title"] = _clean(_txt(ch), 300)
            elif t in ("description", "summary", "content", "encoded"):
                if not d["desc"]:
                    d["desc"] = _clean(_txt(ch))
            elif t == "link":
                href = ch.get("href") or _txt(ch)
                if href and not d["url"]:
                    d["url"] = href.strip()
            elif t in ("pubdate", "published", "updated", "date"):
                d["ts"] = d["ts"] or _ts(_txt(ch))
            elif t == "source":
                d["orig_source"] = _clean(_txt(ch), 60)
        if not d["title"]:
            continue
        if not d["url"]:
            g = el.find("{http://www.w3.org/2005/Atom}id")
            if g is not None and (g.text or "").strip().startswith("http"):
                d["url"] = g.text.strip()
        if not d["desc"]:
            d["desc"] = d["title"]
        items.append(d)
    return items


if __name__ == "__main__":
    import sys
    src = load_sources(sys.argv[1] if len(sys.argv) > 1
                       else "/var/minis/shared/osint/sources.txt")
    print(f"sources: {len(src)}")
    cat, name, url = src[0]
    its = parse_feed(fetch(url))
    print(f"{name}: {len(its)} items")
    for it in its[:3]:
        print("-", it["ts"], it["title"][:90], "|", it["url"][:70])
