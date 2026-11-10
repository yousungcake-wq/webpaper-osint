#!/usr/bin/env python3
"""채널 반응(👍/👎) 수집 → 주간 점검용 보고 → 점수 보정.

왜 웹 페이지를 긁는가
- Bot API 는 채널 글의 반응을 **조회하는 메서드가 없다**. `message_reaction` 갱신은
  관리자인 그룹/채널에서 봇이 받는 푸시일 뿐이고, 지난 반응은 되돌아오지 않는다.
- 공개 채널이면 t.me 웹 미리보기(t.me/s/<채널>, ?embed=1)에 **게시물별 반응 수**가
  그대로 실려 있다. 토큰도, 사용자 계정(MTProto)도 필요 없다. 2026-11-10 확인.

산출물
- state/feedback.jsonl : 게시물 1건 = 1줄 (m=메시지 id, rx={이모지: 개수})
- config.json 의 feedback : 소스/비트별 점수 보정값. osint.fb_adj() 가 읽는다.

사용
    python3 feedback.py fetch --days 21      # 반응 긁어서 병합
    python3 feedback.py report --days 21     # 지적된 기사·패턴 진단
    python3 feedback.py apply [--dry-run]    # 보정값 계산해 config.json 반영
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
STATE_DIR = os.path.join(BASE, "state")
FB_PATH = os.path.join(STATE_DIR, "feedback.jsonl")
CH = "webpaperHQ"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
DISLIKE = "👎"
LIKE = "👍"

BLOCK = re.compile(r'<div class="tgme_widget_message[^"]*" data-post="' + CH + r'/(\d+)"')


# ── 긁기 ──────────────────────────────────────────────────────────────
def _get(url, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:                       # 순간 오류는 재시도
            last = e
            time.sleep(2 + i)
    raise last


def parse_page(html):
    out = []
    parts = BLOCK.split(html)
    for i in range(1, len(parts) - 1, 2):
        mid, body = int(parts[i]), parts[i + 1]
        dtm = re.search(r'datetime="([^"]+)"', body)
        textm = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                          body, re.S)
        url = None
        if textm:
            for a in re.findall(r'href="(https?://[^"]+)"', textm.group(1)):
                if "t.me" not in a and "telegram.org" not in a:
                    url = a.replace("&amp;", "&")
                    break
        react = {}
        rm = re.search(r'tgme_widget_message_reactions[^>]*>(.*?)</div>\s*<div', body, re.S)
        if rm:
            for emoji, cnt in re.findall(r'<b>(.*?)</b></i>(\d+)?', rm.group(1)):
                react[emoji] = react.get(emoji, 0) + int(cnt or 1)
        out.append({"m": mid, "dt": dtm.group(1) if dtm else None, "url": url, "rx": react})
    return out


def scrape(since):
    """since(UTC datetime) 이후 게시물을 최신부터 거슬러 긁는다."""
    posts, before, empty = {}, None, 0
    for _ in range(200):                              # 최대 4000건
        url = f"https://t.me/s/{CH}" + (f"?before={before}" if before else "")
        got = parse_page(_get(url))
        if not got:
            empty += 1
            if empty >= 2:
                break
            time.sleep(2)
            continue
        empty = 0
        for p in got:
            posts[p["m"]] = p
        before = min(posts) if before is None else min(before, min(p["m"] for p in got))
        oldest = min(p["dt"] for p in got if p["dt"])
        if _ts(oldest) < since.timestamp():
            break
        time.sleep(0.7)
    return posts


# ── 상태(게시 기록)와 잇기 ────────────────────────────────────────────
def _ts(s):
    if not s:
        return 0
    s = s.replace(" UTC", "").replace("T", " ")[:19]
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return 0


def state_rows():
    import run_once
    return run_once._rows()


def match(posts, rows):
    """게시물 ↔ 상태행. URL 정규화 일치를 먼저, 실패하면 시각(±150초) 최근접."""
    import filters
    by_url = {}
    for r in rows:
        for u in (r.get("cu") or "", r.get("u") or ""):
            if u:
                by_url.setdefault(filters.canon_url(u), r)
    unmatched = []
    for p in posts.values():
        row = by_url.get(filters.canon_url(p["url"] or "")) if p["url"] else None
        if row is None:
            unmatched.append(p)
        else:
            _fill(p, row)
    rows_t = sorted([(_ts(r.get("a")), r) for r in rows if r.get("a")], key=lambda x: x[0])
    used = set()
    for p in unmatched:
        if not p["dt"]:
            continue
        t = _ts(p["dt"])
        cand = [(abs(rt - t), idx, r) for idx, (rt, r) in enumerate(rows_t)
                if abs(rt - t) <= 150 and idx not in used]
        if cand:
            cand.sort()
            _used_idx, r = cand[0][1], cand[0][2]
            used.add(_used_idx)
            _fill(p, r)
            p["how"] = "time"
    return posts


def _fill(p, r):
    p.update({"u": r.get("u") or p.get("url"), "k": r.get("k"), "t": r.get("t"),
              "f": r.get("f"), "b": r.get("b"), "r": r.get("r"), "s": r.get("s"),
              "a": r.get("a"), "how": "url"})


def merge(posts):
    old = {}
    if os.path.exists(FB_PATH):
        for line in open(FB_PATH, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    d = json.loads(line)
                    old[d["m"]] = d
                except Exception:
                    pass
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for m, p in posts.items():
        rec = old.get(m, {"m": m})
        for k in ("u", "k", "t", "f", "b", "r", "s", "a", "how"):
            if p.get(k) is not None:
                rec[k] = p[k]
        rec["d"] = p.get("dt") or rec.get("d")
        rec["rx"] = p.get("rx") or {}                 # 반응은 최신값으로 교체(취소 반영)
        if rec["rx"]:
            rec["seen"] = now
        old[m] = rec
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(FB_PATH, "w", encoding="utf-8") as f:
        for m in sorted(old):
            f.write(json.dumps(old[m], ensure_ascii=False) + "\n")
    return old


def load_fb():
    out = []
    if not os.path.exists(FB_PATH):
        return out
    for line in open(FB_PATH, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def window(days, fb=None):
    cut = time.time() - days * 86400
    return [d for d in (fb or load_fb()) if _ts(d.get("d") or d.get("a")) >= cut]


# ── 진단: 기존 필터가 잡을 수 있었나 ──────────────────────────────────
def diagnose(title, url):
    import filters
    import osint
    hits = []
    t = title or ""
    if osint.JUNK_TITLE.search(t):
        hits.append("JUNK_TITLE")
    if osint.NOISE_TITLE.search(t):
        hits.append("NOISE_TITLE")
    if osint.LOW_VALUE.search(t) or osint.LOW_VALUE_ANY.search(t):
        hits.append("LOW_VALUE")
    if osint.PRICE_LIST.search(t):
        hits.append("PRICE_LIST")
    if osint.LOCAL_MARKET.search(t):
        hits.append("LOCAL_MARKET")
    if osint.COURTESY.search(t):
        hits.append("COURTESY")
    r = filters.is_digest(t, url or "")
    if r:
        hits.append(f"digest:{r}")
    if osint.junk_item(t, url or ""):
        hits.append("junk_item")
    return hits


# ── 보고 ──────────────────────────────────────────────────────────────
def report(days):
    fb = window(days)
    n_post = len(fb)
    reacted = [d for d in fb if d.get("rx")]
    dis = [d for d in reacted if d["rx"].get(DISLIKE)]
    lik = [d for d in reacted if d["rx"].get(LIKE)]
    print(f"■ 최근 {days}일: 게시 {n_post}건 / 반응 있는 글 {len(reacted)}건 "
          f"(👎 {len(dis)} · 👍 {len(lik)})")
    if dis:
        print("\n■ 👎 받은 기사")
        for d in sorted(dis, key=lambda x: x["d"] or "", reverse=True):
            hits = diagnose(d.get("t"), d.get("u"))
            print(f"  {d['m']} {d.get('d','')[5:16]} {'👎'*d['rx'][DISLIKE]} [{d.get('f','?')}/"
                  f"{d.get('b','?')}] {(d.get('t') or '')[:78]}")
            print(f"        필터: {', '.join(hits) if hits else '없음 ← 새 규칙 후보'}")
            print(f"        {(d.get('u') or '')[:110]}")
    if lik:
        print("\n■ 👍 받은 기사")
        for d in sorted(lik, key=lambda x: x["d"] or "", reverse=True)[:15]:
            print(f"  {d['m']} {(d.get('t') or '')[:80]} [{d.get('f','?')}/{d.get('b','?')}]")
    for label, key in (("소스", "f"), ("비트", "b"), ("지역", "r")):
        agg = {}
        for d in fb:
            k = d.get(key) or "?"
            a = agg.setdefault(k, [0, 0, 0])
            a[0] += 1
            a[1] += d["rx"].get(DISLIKE, 0)
            a[2] += d["rx"].get(LIKE, 0)
        rows = [(k, v) for k, v in agg.items() if v[1] or v[2]]
        if not rows:
            continue
        rows.sort(key=lambda x: -x[1][1])
        print(f"\n■ {label}별 (게시 / 👎 / 👍)")
        for k, v in rows[:12]:
            print(f"  {k:<28} {v[0]:>4}  👎{v[1]:>2}  👍{v[2]:>2}")
    return len(dis)


# ── 보정값 ────────────────────────────────────────────────────────────
def compute(days=21):
    """소스·비트별 점수 보정. 표본이 작으므로 근거가 있을 때만, 상한을 둔다.

    adj = -2.5*👎 + 1.0*👍  (소스),  -1.5*👎 + 0.5*👍  (비트)
    소스는 👎 1건부터, 비트는 2건부터 움직인다. 범위 소스 [-4,+2] / 비트 [-3,+1].
    창을 벗어난 반응은 자동으로 사라지므로 보정은 스스로 되돌아간다.
    """
    fb = window(days)
    src, beat = {}, {}
    for d in fb:
        dis = d["rx"].get(DISLIKE, 0) if d.get("rx") else 0
        lik = d["rx"].get(LIKE, 0) if d.get("rx") else 0
        if not (dis or lik):
            continue
        s = d.get("f") or "?"
        a = src.setdefault(s, [0, 0])
        a[0] += dis
        a[1] += lik
        b = d.get("b") or "?"
        c = beat.setdefault(b, [0, 0])
        c[0] += dis
        c[1] += lik
    out_s, out_b = {}, {}
    for k, (dis, lik) in src.items():
        if dis >= 1 or lik >= 2:
            v = round(max(-4.0, min(2.0, -2.5 * dis + 1.0 * lik)), 1)
            if v:
                out_s[k] = v
    for k, (dis, lik) in beat.items():
        if dis >= 2 or lik >= 3:
            v = round(max(-3.0, min(1.0, -1.5 * dis + 0.5 * lik)), 1)
            if v:
                out_b[k] = v
    return out_s, out_b, len(fb)


def apply(days=21, dry=False):
    cfgp = os.path.join(BASE, "config.json")
    cfg = json.load(open(cfgp, encoding="utf-8"))
    src, beat, n = compute(days)
    prev = cfg.get("feedback") or {}
    print(f"게시 {n}건 기준 · 소스 {len(src)}곳 · 비트 {len(beat)}개 보정")
    for label, new, old in (("소스", src, prev.get("src", {})), ("비트", beat, prev.get("beat", {}))):
        for k in sorted(set(new) | set(old)):
            o, v = old.get(k, 0), new.get(k, 0)
            if o != v:
                print(f"  {label} {k:<26} {o:+.1f} → {v:+.1f}")
    if dry or (src == prev.get("src", {}) and beat == prev.get("beat", {})):
        return
    cfg["feedback"] = {"src": src, "beat": beat, "window_days": days,
                       "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    json.dump(cfg, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("config.json 갱신")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--days", type=int, default=21)
    r = sub.add_parser("report")
    r.add_argument("--days", type=int, default=21)
    a = sub.add_parser("apply")
    a.add_argument("--days", type=int, default=21)
    a.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.cmd == "fetch":
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
        posts = scrape(since)
        rows = state_rows()
        match(posts, rows)
        allfb = merge(posts)
        got = [p for p in posts.values() if p["rx"]]
        nof = [p for p in posts.values() if p["rx"] and not p.get("f")]
        print(f"긁음 {len(posts)}건 / 반응 {len(got)}건 / 상태 미연결 {len(nof)}건 "
              f"| feedback.jsonl {len(allfb)}줄")
        for p in sorted(got, key=lambda x: -x["m"])[:20]:
            print(f"  {p['m']} {p['rx']} {p.get('t','(미연결)')[:60]}")
    elif args.cmd == "report":
        report(args.days)
    else:
        apply(args.days, args.dry_run)


if __name__ == "__main__":
    main()
