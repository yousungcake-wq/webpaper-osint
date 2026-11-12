#!/usr/bin/env python3
"""taste.py — 반응(👎/👍)에서 '취향 축'을 학습해 채점에 반영한다 (2026-11-10).

왜 소스가 아니라 내용인가
- 첫 시도는 소스 벌점이었다. 사용자가 바로 지적했다: "싫은 건 소스가 아니라 뉴스다."
  실제로 한 매체가 좋은 기사와 나쁜 기사를 같이 낸다(Guardian 의 Cop31 live: 는 👎,
  같은 매체의 다른 기사는 문제 없음). 그래서 매체 단위 벌점은 버리고 **내용 축**으로 간다.

어떻게 학습하나 (표본이 작다는 걸 전제로)
- 라벨 = 사람이 실제로 본 글. 반응이 없으면 중립(0). 표본 구간은 feedback.py sample 로 등록한다.
- 축마다 기대 👎 수(p0·n)보다 실제 👎 가 얼마나 많은지로 벌점을 정한다:

      excess = d - p0·n
      w = clamp(-2.0 · excess / sqrt(n+1), -3.5, +1.5)

  글 수 n 이 그대로 근거의 세기가 된다. n=2,d=2 면 -0.8 수준의 넛지고,
  n=8,d=7 이면 -2.5 로 굳는다. 표본이 쌓이면 같은 규칙이 저절로 날카로워진다.
- 👎는 글마다 축이 2~3개씩 겹친다(나이지리아+원자재+시장). 합산은 -5~+2.5 로 자른다.

사용
    python3 taste.py learn          # 표본 라벨 → config.json 의 taste
    python3 taste.py report         # 축별 표 + 최근 게시분에 미치는 영향
"""
import json
import math
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
CFG = os.path.join(BASE, "config.json")
LIMIT_LO, LIMIT_HI = -5.0, 2.5
BLOCK_AT = -2.5      # 여러 축이 겹쳐 이보다 밀리면 게시하지 않는다 (한 축만으로는 안 막힌다)

# 취향 축 — 제목+요약에서 찾는다. 정규식은 의도적으로 좁게(오탐이 쌓이면 축이 죽는다).
AXES = {
    "사건·공격": r"(?i)\b(attack\w*|airstrike|strike|killed|dead|cyber\w*|hack\w*|bomb\w*|"
                r"clash\w*|raid|explosion|crash|wounded|war|offensive)\b",
    "대응·제재": r"(?i)\b(vow\w*|warn\w*|sanction\w*|retaliat\w*|threaten\w*|expel\w*|"
                r"ceasefire|ultimatum|mobiliz\w*)\b",
    "시장·기업": r"(?i)\b(earnings?|loss|profit\w*|revenue|acqui\w*|merger|ipo|shares?|"
                r"bond|yield\w*|etf|stake|writedown|order book|dividend|buyback)\b",
    "기술·AI": r"(?i)\b(ai|nvidia|chip\w*|semiconduct\w*|data cent\w*|robot\w*|cloud|"
                r"model\w*|algorithm\w*)\b",
    "정책·인선": r"(?i)\b(appoint\w*|nominee|frontrunner|successor|to lead|cabinet|"
                r"minister|central bank|ecb|fed|rate (cut|hike))\b",
    "예측·가정": r"(?i)(\bif \b|\bwould\b|\bscenario\w*|forecast\w*|outlook|predict\w*|"
                r"expected to|\bwill \w+ \w+ \?)",
    "선거·관측": r"(?i)\b(election\w*|ballot|candidate\w*|primary|vote\w*|coalition)\b|"
                r"\b20\d\d\s*:",
    "행사·홍보": r"(?i)\b(summit|pavilion|expo|forum|showcase\w*|unveil\w*|booth|"
                r"keynote|conference|press release)\b",
    "원자재·에너지": r"(?i)\b(oil|gas|lng|refin\w*|opec|brent|wti|crude|gold|copper|"
                    r"mining|mine)\b",
    # 2026-11-11: 지역 축은 지운다. 아프리카 비분쟁 차단은 osint._candidate_ok 의
    # 하드 규칙(africa_non_conflict)이 맡는다. 취향 축으로 두면 나이지리아 '분쟁' 기사까지
    # 같이 밀려서 사용자가 원하는 것을 막게 된다.
}
COMPILED = {k: re.compile(v) for k, v in AXES.items()}

_CACHE = {}


def _config():
    try:
        mt = os.path.getmtime(CFG)
    except OSError:
        mt = 0
    if _CACHE.get("mt") != mt:
        try:
            _CACHE["v"] = json.load(open(CFG, encoding="utf-8"))
        except Exception:
            _CACHE["v"] = {}
        _CACHE["mt"] = mt
    return _CACHE["v"]


def hits(title, body=""):
    txt = f"{title or ''} {body or ''}"
    return [k for k, rx in COMPILED.items() if rx.search(txt)]


def blocked(title, body=""):
    """취향상 내보내지 않을 글인가. 축이 하나만 걸리면 최대 -3.5 지만,
    실제로 -2.5 아래로 가려면 대개 두 축 이상이 겹친다(= 근거가 겹친 경우)."""
    return adj(title, body) <= BLOCK_AT


def adj(title, body=""):
    """채점에 더할 값. 학습 전이면 0."""
    w = (_config().get("taste") or {}).get("axes") or {}
    if not w:
        return 0.0
    v = sum(w.get(k, 0.0) for k in hits(title, body))
    return max(LIMIT_LO, min(LIMIT_HI, v))


# ── 학습 ──────────────────────────────────────────────────────────────
def learn(days=120, apply_it=True):
    import feedback as F
    lab = F.labeled(days)
    if not lab:
        print("라벨 없음 — feedback.py sample 로 표본을 먼저 등록한다")
        return None
    n_lab = len(lab)
    d_tot = sum(1 for d in lab if d["lab"] == 1)
    p0 = d_tot / n_lab
    stat = {}
    for d in lab:
        for k in hits(d.get("t"), d.get("x")):
            s = stat.setdefault(k, [0, 0, 0])            # n, 👎, 👍
            s[0] += 1
            s[1] += 1 if d["lab"] == 1 else 0
            s[2] += 1 if d["lab"] == -1 else 0
    axes = {}
    for k, (n, dis, lik) in stat.items():
        excess = dis - p0 * n
        w = -2.0 * excess / math.sqrt(n + 1)
        if w > 0:
            w *= 0.5          # '싫지 않았다'는 '좋다'보다 약한 증거다 (👎만 명시 신호)
        w = round(max(-3.5, min(1.5, w)), 1)
        if abs(w) >= 0.2:
            axes[k] = w
    out = {"axes": axes, "p0": round(p0, 3), "n_labels": n_lab,
           "window_days": days,
           "at": __import__("datetime").datetime.now(
               __import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    print(f"라벨 {n_lab}건 (👎{d_tot}) · 기본 👎율 {p0:.0%}")
    print(f"  {'축':<16} {'해당':>4} {'👎':>3} {'👍':>3} {'가중치':>7}")
    for k, (n, dis, lik) in sorted(stat.items(), key=lambda x: -x[1][1]):
        print(f"  {k:<16} {n:>4} {dis:>3} {lik:>3} {axes.get(k, 0):>+7.1f}")
    if apply_it:
        cfg = _config()
        cfg["taste"] = out
        json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _CACHE.clear()
        print("config.json 의 taste 갱신")
    return out


def report(days=7, show=10):
    import feedback as F
    fb = F.window(days)
    w = (_config().get("taste") or {}).get("axes") or {}
    print(f"■ 학습된 취향 축 (config.json): "
          f"{(_config().get('taste') or {}).get('at', '없음')}")
    if not w:
        print("  (아직 학습 안 됨 — python3 taste.py learn)")
    for k, v in sorted(w.items(), key=lambda x: x[1]):
        print(f"  {k:<16} {v:+.1f}")
    if not w:
        return
    touched = []
    for d in fb:
        v = adj(d.get("t") if "t" in d else d.get("t"), d.get("x") or "")
        if abs(v) >= 0.5:
            touched.append((v, d))
    touched.sort(key=lambda x: x[0])
    print(f"\n■ 최근 {days}일 게시 {len(fb)}건 중 취향 축이 흔든 글 {len(touched)}건")
    for v, d in touched[:show]:
        print(f"  {v:+.1f} {d['m']} [{(d.get('f') or '?')[:16]}] {(d.get('t') or '')[:62]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "learn":
        learn()
    else:
        report(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
