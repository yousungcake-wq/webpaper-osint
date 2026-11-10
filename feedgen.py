#!/usr/bin/env python3
"""feedgen.py — state/posts → data/feed.json (사이트가 읽는 정적 피드)

왜 별도 파일인가
  · state/posts/*.jsonl 은 한 줄 = 게시 1건이지만 사이트가 그대로 읽기엔 너무 원시적이다
    (요약 없음·키 이름 한 글자·시각 형식 제각각).
  · 사이트는 정적 호스팅(무료)에서 돌기 때문에 서버가 없다. 데이터도 파일이어야 한다.
  · 페이지를 다시 배포하지 않고 **데이터만** 갱신하려고 feed.json 을 site/ 밖(data/)에 둔다.
    5분마다 배포하면 GitHub Pages 빌드 한도(시간당 10회)에 걸린다.

계약(사이트가 읽는 스키마)
  { "generated_at": ISO, "count": N,
    "items": [ {"h":한국어제목|"", "t":원문제목, "summary":요약|"", "url":URL|"",
                "src":["매체"], "b":비트, "r":지역, "ts":ISO} ] }
  점수(s)와 원시 시각(a)은 싣지 않는다 — 화면에 쓰지 않는 값까지 5분마다 커밋하면
  저장소만 무거워진다.

  python3 feedgen.py            # data/feed.json 갱신
  python3 feedgen.py --check    # 현황만 출력
"""
import argparse, glob, json, os, sys
import datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
POSTS = os.path.join(BASE, "state", "posts")
OUT = os.path.join(BASE, "data", "feed.json")
DAYS = 5          # 사이트에 남길 기간
MAX_ITEMS = 300   # 한 번에 내려줄 최대 건수 (모바일 파싱·전송 부담)
BEATS = ("TECH", "FINANCE", "CONFLICT", "ECONOMY", "COMMODITY", "POLITICS")
REGIONS = ("US", "LATAM", "EUROPE", "ASIA", "MIDDLE_EAST", "AFRICA", "GLOBAL")


def _iso(a):
    """"2026-11-10 09:20 UTC" → "2026-11-10T09:20:00Z" (사이트가 Date 로 파싱한다)"""
    try:
        return dt.datetime.strptime(a.replace(" UTC", ""), "%Y-%m-%d %H:%M").strftime(
            "%Y-%m-%dT%H:%M:00Z")
    except Exception:
        return ""


def _rows():
    for path in sorted(glob.glob(os.path.join(POSTS, "*", "*.jsonl"))):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
        except OSError:
            continue


def build(days=DAYS, max_items=MAX_ITEMS):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    seen_key, seen_url, items = set(), set(), []
    for r in _rows():
        a = r.get("a") or ""
        if a[:10] < cutoff:
            continue
        k = r.get("k") or ""
        cu = r.get("cu") or ""
        if k and k in seen_key:
            continue
        if cu and cu in seen_url:
            continue
        seen_key.add(k)
        seen_url.add(cu)
        beat = (r.get("b") or "").upper()
        if beat not in BEATS:
            beat = ""
        region = (r.get("r") or "").upper()
        if region not in REGIONS:
            region = "GLOBAL"
        items.append({
            "h": (r.get("h") or "").strip()[:80],
            "t": (r.get("t") or "").strip()[:180],
            "summary": (r.get("x") or "").strip()[:1200],
            "url": r.get("u") or "",
            "src": [r.get("f")] if r.get("f") else [],
            "b": beat,
            "r": region,
            "a": a,          # 정렬용 — 출력 직전에 지운다
            "ts": _iso(a),
        })
    items.sort(key=lambda x: (x["a"], float(x.get("s") or 0)), reverse=True)
    dropped = max(0, len(items) - max_items)
    items = items[:max_items]
    for i in items:
        i.pop("a", None)
    data = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(items),
        "days": days,
        "items": items,
    }
    return data, dropped


def write(path=OUT):
    data, dropped = build()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return data, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 현황만 본다")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    data, dropped = build()
    no_head = sum(1 for i in data["items"] if not i["h"])
    no_sum = sum(1 for i in data["items"] if not i["summary"])
    no_url = sum(1 for i in data["items"] if not i["url"])
    print(f"items {data['count']} (한도 초과로 버림 {dropped}) · 기간 {data['days']}일")
    print(f"  제목없음 {no_head} · 요약없음 {no_sum} · URL없음 {no_url}")
    if not a.check:
        data, dropped = write(a.out)
        print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes, "
              f"생성 {data['generated_at']})")


if __name__ == "__main__":
    main()
