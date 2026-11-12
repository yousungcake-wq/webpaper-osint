#!/usr/bin/env python3
"""feedgen.py — state/posts → data/feed.json (사이트가 읽는 정적 피드)

왜 별도 파일인가
  · state/posts/*.jsonl 은 한 줄 = 게시 1건이지만 사이트가 그대로 읽기엔 너무 원시적이다
    (요약 없음·키 이름 한 글자·시각 형식 제각각).
  · 사이트는 정적 호스팅(무료)에서 돌기 때문에 서버가 없다. 데이터도 파일이어야 한다.
  · feed.json 은 site/ 안에 두고 **페이지와 같이 배포한다**(2026-11-10 변경).
    GitHub 문서: "10 builds per hour ... This limit does not apply if you build and publish
    your site with a custom GitHub Actions workflow." → Actions 배포는 빌드 한도가 없다.
    게시 → 커밋 → 배포(15~30초)가 한 흐름이라 사이트가 1분 안에 갱신된다.

계약(사이트가 읽는 스키마)
  { "generated_at": ISO, "count": N, "page": P, "pages": M, "total": T, "days": D,
    "items": [ {"h":한국어제목|"", "t":원문제목, "summary":요약|"", "url":URL|"",
                "src":["매체"], "b":"TECH|ECONOMY|CONFLICT|POLITICS|OTHER", "r":지역,
                "s":중요도(소수), "ts":ISO} ] }
  원시 시각(a)은 싣지 않는다(ts 로 대체). s 는 속보 티커의 '중요도' 정렬에 쓴다.

쪽 나누기 (2026-11-12)
  예전엔 한 파일에 최근 300건만 담아 5일 창을 다 못 내려줬다 — 11-11 07:36 이
  '마지막 기사'처럼 보인 이유(하루 180~220건 게시 = 300건이면 1.5일치).
  지금은 5일 창 전체를 PAGE_ITEMS 단위로 쪽을 나눠 쓴다.
    0쪽 = site/feed.json (첫 화면이 바로 받는 것)
    n쪽 = site/feed-pN.json (아래로 내려가면 브라우저가 그때 받는다)
  페이지 수가 줄면 남은 feed-p*.json 은 지운다(오래된 쪽이 유령으로 남지 않게).

  python3 feedgen.py            # data/feed.json 갱신
  python3 feedgen.py --check    # 현황만 출력
"""
import argparse, glob, json, os, sys
import datetime as dt

BASE = os.path.dirname(os.path.abspath(__file__))
POSTS = os.path.join(BASE, "state", "posts")
OUT = os.path.join(BASE, "site", "feed.json")
DAYS = 5            # 사이트에 남길 기간
PAGE_ITEMS = 300    # 한 파일(한 쪽)에 담는 최대 건수 — 724B/건 → 1쪽 ≈ 210KB
# 2026-11-10 비트 통합: 금융·원자재 → 경제, 나머지는 기타(OTHER)
BEATS = ("TECH", "ECONOMY", "CONFLICT", "POLITICS", "OTHER")
LEGACY_BEAT = {"FINANCE": "ECONOMY", "COMMODITY": "ECONOMY", "MARKETS": "ECONOMY"}
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


def _build(days=DAYS):
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
        beat = (r.get("b") or "").strip().upper()
        beat = LEGACY_BEAT.get(beat, beat)
        if beat not in BEATS:
            beat = "OTHER"          # 분류가 없던 옛 글도 '기타'로는 볼 수 있게
        region = (r.get("r") or "").upper()
        if region not in REGIONS:
            region = "GLOBAL"
        items.append({
            "h": (r.get("h") or "").strip()[:80],
            "t": (r.get("t") or "").strip()[:180],
            "summary": (r.get("x") or "").strip()[:1200],
            "url": r.get("u") or "",
            "s": round(float(r.get("s") or 0), 1),      # 중요도(속보 티커 정렬용)
            "src": [r.get("f")] if r.get("f") else [],
            "b": beat,
            "r": region,
            "a": a,          # 정렬용 — 출력 직전에 지운다
            "ts": _iso(a),
        })
    items.sort(key=lambda x: (x["a"], float(x.get("s") or 0)), reverse=True)
    for i in items:
        i.pop("a", None)
    return items


def _npages(n):
    return max(1, (n + PAGE_ITEMS - 1) // PAGE_ITEMS)


def page_path(path, n):
    """0쪽 = feed.json, n쪽 = feed-pN.json (같은 디렉터리)"""
    if not n:
        return path
    d, _ = os.path.split(path)
    return os.path.join(d, "feed-p%d.json" % n)


def _payload(items, page, pages, total):
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(items),
        "page": page,
        "pages": pages,
        "total": total,
        "days": DAYS,
        "items": items,
    }


def build(days=DAYS):
    """5일 창 전체를 최신순으로. 쪽 나누기는 write() 가 한다."""
    items = _build(days)
    pages = _npages(len(items))
    return _payload(items[:PAGE_ITEMS], 0, pages, len(items))


def write(path=OUT):
    items = _build()
    pages = _npages(len(items))
    first = None
    written = []
    for p in range(pages):
        data = _payload(items[p * PAGE_ITEMS:(p + 1) * PAGE_ITEMS], p, pages, len(items))
        out = page_path(path, p)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, out)
        written.append(out)
        if not p:
            first = data
    # 쪽 수가 줄었으면 남은 feed-p*.json 을 지운다(유령 쪽이 남지 않게)
    d, _ = os.path.split(path)
    for stale in glob.glob(os.path.join(d, "feed-p*.json")):
        try:
            n = int(os.path.basename(stale).split("-p")[1].split(".")[0])
        except (IndexError, ValueError):
            continue
        if n >= pages:
            os.remove(stale)
    return first, written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 현황만 본다")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    items = _build()
    pages = _npages(len(items))
    no_head = sum(1 for i in items if not i["h"])
    no_sum = sum(1 for i in items if not i["summary"])
    no_url = sum(1 for i in items if not i["url"])
    span = f"{items[-1]['ts'][:16]} ~ {items[0]['ts'][:16]}" if items else "—"
    print(f"items {len(items)} · {DAYS}일 창 · {pages}쪽 (1쪽 최대 {PAGE_ITEMS})")
    print(f"  범위 {span}")
    print(f"  제목없음 {no_head} · 요약없음 {no_sum} · URL없음 {no_url}")
    if not a.check:
        data, written = write(a.out)
        for w in written:
            print(f"wrote {w} ({os.path.getsize(w) // 1024} KB)")
        print(f"생성 {data['generated_at']}")


if __name__ == "__main__":
    main()
