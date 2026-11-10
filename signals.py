#!/usr/bin/env python3
"""feedback.signals — 어떤 신호가 👎/👍 를 가르는지 누적 집계한다.

라벨(반응)은 사용자 한 명 것이라 표본이 작다. 그래서 이건 '결론 계산기'가 아니라
**가설 검사기**다. 신호마다 라벨된 글 수·👎·👍·중립과 👎율을 내고, 라벨이 쌓이면
어느 쪽 가설이 맞는지 바로 보이게 한다. 지금 표본이 작으면 작다고 말해준다.
"""
import json
import re
import time

DISLIKE = "👎"
LIKE = "👍"

# 후보 신호: 이름 → 판정 함수(제목, URL, 레코드)
SIGNALS = {
    "라이브블로그/라운드업": lambda t, u, d: bool(__import__("filters").is_digest(t, u)),
    "분석·전망 프레이밍": lambda t, u, d: bool(re.search(
        r"(?i)(\bif .{0,40}\b(knocked out|wins|steps down|is barred|were to)\b|"
        r"\bexposes (the )?limits\b|what it means for|\bscenario\b)", t or "")),
    "물음표 제목": lambda t, u, d: bool(re.search(r"\?\s*$", t or "")),
    "티커·투자집계": lambda t, u, d: bool(re.search(r"\([A-Z]{1,5}:[A-Z]{2,6}\)", t or "")),
    "나이지리아 키워드": lambda t, u, d: bool(re.search(
        r"(?i)nigeri|naira|tinubu|atiku|abuja|lagos|dangote", (t or "") + " " + (u or ""))),
    "한국어 소스": lambda t, u, d: bool(re.search(r"[가-힣]{2,}", d.get("f") or "")),
    "점수<15": lambda t, u, d: (d.get("s") or 0) < 15,
    "점수>=22": lambda t, u, d: (d.get("s") or 0) >= 22,
}


def _rate(a, b):
    return (100.0 * a / b) if b else 0.0


def signals(days=21, show=20):
    import feedback as F
    fb = F.window(days)
    labeled = [d for d in fb if d.get("rx")]
    dis = sum(1 for d in labeled if d["rx"].get(DISLIKE))
    lik = sum(1 for d in labeled if d["rx"].get(LIKE))
    neutral = len(labeled) - dis - lik
    base = _rate(dis, len(labeled)) if labeled else 0.0
    print(f"■ 라벨 {len(labeled)}건 (👎{dis} · 👍{lik} · 중립{neutral}) · 전체 👎율 {base:.0f}%")
    if len(labeled) < 8:
        print("  ⚠ 표본이 작다 — 아래 숫자는 순위가 아니라 방향 힌트로만 읽을 것")

    rows = []
    for name, fn in SIGNALS.items():
        hit = [d for d in labeled if fn(d.get("t") or "", d.get("u") or "", d)]
        if not hit:
            continue
        hd = sum(1 for d in hit if d["rx"].get(DISLIKE))
        hl = sum(1 for d in hit if d["rx"].get(LIKE))
        rows.append((hd, hl, len(hit), name))
    # 라벨(지역·비트·소스)도 같은 방식으로
    for key, title in (("r", "지역"), ("b", "비트"), ("f", "소스")):
        agg = {}
        for d in labeled:
            agg.setdefault(d.get(key) or "?", []).append(d)
        for k, v in agg.items():
            hd = sum(1 for d in v if d["rx"].get(DISLIKE))
            hl = sum(1 for d in v if d["rx"].get(LIKE))
            rows.append((hd, hl, len(v), f"{title}:{k}"))
    rows.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    print(f"\n{'신호':<28} {'라벨':>4} {'👎':>3} {'👍':>3} {'👎율':>5}  vs 전체")
    for hd, hl, n, name in rows[:show]:
        r = _rate(hd, n)
        print(f"{name:<28} {n:>4} {hd:>3} {hl:>3} {r:>4.0f}%  {r - base:+.0f}p")


if __name__ == "__main__":
    import sys
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 21
    signals(d)
