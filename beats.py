#!/usr/bin/env python3
"""beats.py — 기사 비트 분류 + 균형 배분 (2026-11-07)

문제: 키워드 점수만으로 뽑으면 점수 인플레이션 때문에 한 분야(분쟁)가 채널을 독점한다.
해법: ① 제목을 6개 비트로 분류(LLM 배치 + 키워드 폴백) ② 최근 게시물의 비트 분포를 보고
     목표 비율에서 가장 모자란 비트를 먼저 채운다.

비트: TECH / FINANCE / CONFLICT / COMMODITY / ECONOMY / POLITICS (+OTHER: 게시 제외)
"""
import json
import os
import re
import time
import urllib.request

BEATS = ["TECH", "FINANCE", "CONFLICT", "COMMODITY", "ECONOMY", "POLITICS"]
TARGET = {"TECH": 2, "FINANCE": 2, "CONFLICT": 2, "ECONOMY": 2,
          "COMMODITY": 1, "POLITICS": 1}          # 10건 기준 목표 비율
WINDOW = 10                                       # 분포를 볼 최근 게시물 수

UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36")

# ── 키워드 1차 분류 (LLM 이 실패해도 동작) ──────────────────────────
KW = {
    "TECH": ["ai", "artificial intelligence", "openai", "anthropic", "nvidia", "chip",
             "semiconductor", "tsmc", "data center", "datacenter", "cloud", "software",
             "startup", "antitrust", "cyber", "quantum", "robot", "iphone", "android",
             "microsoft", "google", "meta", "amazon", "tesla", "spacex", "algorithm",
             "app", "platform", "silicon", "gpu", "model", "chatbot", "censorship"],
    "FINANCE": ["market", "stocks", "shares", "bond", "yield", "treasury", "nasdaq",
                "s&p", "dow", "ipo", "merger", "acquisition", "earnings", "hedge fund",
                "private equity", "bank", "lender", "credit", "default", "insurer",
                "selloff", "rally", "valuation", "investor", "dividend", "etf"],
    "CONFLICT": ["war", "strike", "airstrike", "missile", "drone", "troops", "militia",
                 "militant", "insurgen", "offensive", "ceasefire", "hostage", "killed",
                 "attack", "bomb", "shelling", "refugee", "displaced", "famine", "massacre",
                 "houthi", "rsf", "m23", "hezbollah", "hamas", "junta", "coup", "sanctions",
                 "terror", "military", "defence", "defense", "nuclear", "cease-fire"],
    "COMMODITY": ["oil", "brent", "wti", "crude", "opec", "gas", "lng", "gold", "silver",
                  "copper", "lithium", "uranium", "nickel", "cobalt", "iron ore",
                  "rare earth", "wheat", "corn", "soybean", "cocoa", "coffee", "sugar",
                  "commodit", "pipeline", "refinery", "mining", "mine", "shipping rate"],
    "ECONOMY": ["economy", "economic", "gdp", "inflation", "deflation", "recession",
                "unemployment", "jobs report", "payroll", "central bank", "interest rate",
                "rate cut", "rate hike", "fed", "ecb", "boj", "trade deal", "tariff",
                "export", "import", "budget", "deficit", "stimulus", "productivity",
                "retail sales", "housing", "manufacturing", "pmi"],
    "POLITICS": ["election", "vote", "parliament", "president", "prime minister",
                 "summit", "diplomat", "foreign minister", "treaty", "coalition",
                 "referendum", "nato", "eu", "united nations", "g7", "g20", "brics",
                 "opposition", "legislation", "congress", "senate", "policy", "border",
                 "immigration", "protest", "embassy", "ambassador"],
    "OTHER": ["celebrity", "football", "soccer", "cricket", "world cup", "premier league",
              "k-pop", "box office", "taylor swift", "nba", "nfl", "olympic", "tennis",
              "golf", "fashion", "reality show", "award", "horoscope", "recipe",
              "royal family", "gossip", "obituar", "lottery", "weather forecast",
              "macklemore", "wedding", "chef", "restaurant review"],
}

# 키워드 오분류 방지 (먼저 적용)
OVERRIDES = [
    (r"west bank", "CONFLICT"), (r"\bgaza\b", "CONFLICT"), (r"\bgolan\b", "CONFLICT"),
    (r"\bcentral bank\b", "ECONOMY"), (r"\bbank of (japan|england|korea)\b", "ECONOMY"),
    (r"\bworld bank\b", "ECONOMY"), (r"\bcentral banking\b", "ECONOMY"),
    (r"\bai\b.{0,12}\bchip", "TECH"), (r"data cent(er|re)", "TECH"),
    (r"\bopec\b", "COMMODITY"), (r"\bcrude oil\b", "COMMODITY"),
    (r"\bpresident trump\b.{0,30}tariff", "ECONOMY"),
]

# ── 지역 축 (2026-11-08: "글로벌이 이란·소말리아·나이지리아·미국이 끝이냐" 지적) ──
REGIONS = ["EUROPE", "LATAM", "US", "ASIA", "AFRICA", "MIDDLE_EAST"]
R_TARGET = {"EUROPE": 3, "LATAM": 2, "US": 2, "ASIA": 2, "AFRICA": 2, "MIDDLE_EAST": 1}

REGION_WORDS = {
    "EUROPE": ["europe", "european", "eu", "brussels", "france", "french", "germany",
               "german", "italy", "italian", "spain", "spanish", "poland", "polish",
               "ukraine", "ukrainian", "netherlands", "sweden", "norway", "denmark",
               "finland", "greece", "portugal", "ireland", "austria", "switzerland",
               "serbia", "kosovo", "hungary", "czech", "romania", "bulgaria",
               "baltic", "moldova", "belarus", "britain", "british", "london", "macron"],
    "LATAM": ["brazil", "mexico", "mexican", "argentina", "chile", "colombia", "peru",
              "venezuela", "ecuador", "bolivia", "paraguay", "uruguay", "cuba",
              "haiti", "guatemala", "honduras", "el salvador", "nicaragua", "panama",
              "latin america", "latam", "lula", "milei"],
    "US": ["united states", "u.s.", "washington", "white house", "congress",
           "senate", "pentagon", "trump", "american", "new york", "california"],
    "ASIA": ["china", "chinese", "japan", "japanese", "korea", "korean", "india",
             "indian", "pakistan", "indonesia", "philippines", "vietnam", "thailand",
             "malaysia", "singapore", "taiwan", "bangladesh", "sri lanka", "myanmar",
             "australia", "new zealand"],
    "AFRICA": ["nigeria", "kenya", "ethiopia", "somalia", "sudan", "sahel", "mali",
               "niger", "chad", "congo", "ghana", "south africa", "uganda",
               "tanzania", "mozambique", "zimbabwe", "cameroon", "senegal", "libya",
               "egypt", "morocco", "algeria", "tunisia", "africa", "darfur"],
    "MIDDLE_EAST": ["iran", "israel", "gaza", "lebanon", "syria", "iraq", "yemen",
                    "saudi", "uae", "qatar", "turkey", "jordan", "houthi",
                    "hezbollah", "tehran", "west bank"],
}


def kw_region(title):
    t = (title or "").lower()
    scores = {}
    for r, words in REGION_WORDS.items():
        n = sum(1 for w in words if _hit(w, t))
        if n:
            scores[r] = n
    if not scores:
        return "GLOBAL"
    return max(scores, key=lambda r: scores[r])


def region_deficits(con, window=12):
    rows = con.execute("""SELECT region, COUNT(*) FROM (SELECT region FROM items
        WHERE posted_at IS NOT NULL ORDER BY posted_at DESC LIMIT ?) GROUP BY region""",
                       (window,)).fetchall()
    have = {r: c for r, c in rows}
    return sorted(((r, R_TARGET[r] - have.get(r, 0)) for r in R_TARGET),
                  key=lambda x: -x[1])


CAT_HINT = {"tech": "TECH", "finance": "FINANCE", "conflict": "CONFLICT",
            "economy": "ECONOMY", "politics": "POLITICS"}


_PAT = {}


def _hit(word, text):
    """짧은 단어(<=5자)는 단어 경계로 매칭한다. 'ai'가 'said'에 걸리는 것을 막는다."""
    w = word.strip()
    pat = _PAT.get(w)
    if pat is None:
        if len(w) <= 5:
            body = re.escape(w).replace(r"\ ", r"\s+")
            pat = re.compile(r"(?<![a-z])" + body + r"(?![a-z])")
        else:
            pat = w
        _PAT[w] = pat
    return bool(pat.search(text)) if hasattr(pat, "search") else (pat in text)


def kw_beat(title, cat=""):
    """제목+피드 카테고리로 1차 분류. 키워드가 하나도 없으면 카테고리 힌트."""
    t = (title or "").lower()
    for pat, beat in OVERRIDES:
        if re.search(pat, t):
            return beat
    scores = {}
    for b, words in KW.items():
        s = sum(1 for w in words if _hit(w, t))
        if s:
            scores[b] = s
    if scores:
        # OTHER 가 다른 비트보다 확실히 많을 때만 OTHER
        best = max(scores, key=lambda b: scores[b])
        if best == "OTHER" and scores[best] < 2 and len(scores) > 1:
            scores.pop("OTHER", None)
            best = max(scores, key=lambda b: scores[b])
        return best
    return CAT_HINT.get(cat, "POLITICS")


# ── LLM 배치 분류 ────────────────────────────────────────────────
PROMPT = """다음 뉴스 제목을 아래 비트 중 하나로 분류해라. 설명 없이 "번호:비트" 형식으로만 한 줄씩 답한다.
비트: TECH(기술·AI·반도체·플랫폼), FINANCE(금융·시장·금리·실적·M&A), CONFLICT(전쟁·분쟁·안보·테러), COMMODITY(원자재·에너지·광물·곡물), ECONOMY(거시경제·무역·고용·물가), POLITICS(국제정치·선거·외교), OTHER(연예·스포츠·생활)

{titles}"""


def _post(url, payload, headers, timeout=30):   # 5분 주기 안에 끝나야 한다 (2026-11-08: 90s×폴백이 회차를 5분 이상 물고 있었다)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _llm_labels(titles):
    """제목 리스트 → 비트 리스트. 실패하면 None."""
    body_text = "\n".join(f"{i+1}. {t[:110]}" for i, t in enumerate(titles))
    prompt = PROMPT.format(titles=body_text)

    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        try:
            r = _post("https://api.groq.com/openai/v1/chat/completions",
                      {"model": "openai/gpt-oss-120b", "temperature": 0,
                       "max_tokens": 2000, "reasoning_effort": "low",
                       "messages": [{"role": "user", "content": prompt}]},
                      {"Authorization": f"Bearer {key}"}, timeout=20)
            out = r["choices"][0]["message"]["content"]
            if out and out.strip():
                return _parse(out, len(titles))
        except Exception as e:
            print(f"[beats] groq {type(e).__name__}: {str(e)[:100]}", flush=True)

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        try:
            r = _post("https://generativelanguage.googleapis.com/v1beta/models/"
                      "gemini-3-flash-preview:generateContent?key=" + key,
                      {"contents": [{"parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0, "maxOutputTokens": 2048}},
                      {}, timeout=20)
            out = r["candidates"][0]["content"]["parts"][0]["text"]
            return _parse(out, len(titles))
        except Exception as e:
            print(f"[beats] gemini {type(e).__name__}: {str(e)[:100]}", flush=True)
    return None


def _parse(text, n):
    labels = [None] * n
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s*[:.\)]\s*(TECH|FINANCE|CONFLICT|COMMODITY|ECONOMY|POLITICS|OTHER)",
                     line.strip(), re.I)
        if m:
            i = int(m.group(1)) - 1
            if 0 <= i < n:
                labels[i] = m.group(2).upper()
    if sum(1 for x in labels if x) < n * 0.6:
        return None
    return labels


def backfill_kw(con, limit=1500):
    """키워드 1차 분류로 미분류 기사에 비트를 붙인다(LLM 없이·즉시)."""
    rows = con.execute("""SELECT id, title, cat FROM items
        WHERE posted=0 AND (beat IS NULL OR beat='' OR COALESCE(beat_src,'')!='llm')
        ORDER BY score DESC LIMIT ?""", (limit,)).fetchall()
    for _id, title, cat in rows:
        con.execute("UPDATE items SET beat=?, beat_src='kw' WHERE id=?",
                    (kw_beat(title, cat), _id))
    con.commit()
    return len(rows)


def classify_pending(con, per_beat=10, max_total=60):
    """미분류 기사를 비트별로 골고루 뽑아 한 번에 분류한다.

    점수 상위만 뽑으면 한 분야로 쏠리므로, 키워드 1차 분류로 비트별 후보를 균등하게 만든다.
    """
    rows = con.execute("""SELECT id, title, beat, cat FROM items
        WHERE posted=0 AND COALESCE(beat_src,'')!='llm'
        ORDER BY score DESC LIMIT 400""").fetchall()
    buckets = {}
    for _id, title, beat, cat in rows:
        b = beat or kw_beat(title, cat)
        if b == "OTHER":
            con.execute("UPDATE items SET beat='OTHER', beat_src='kw' WHERE id=?", (_id,))
            continue
        buckets.setdefault(b, []).append(_id)
    con.commit()

    picked = []
    for b in BEATS:
        picked += [(i, b) for i in buckets.get(b, [])[:per_beat]]
    picked = picked[:max_total]
    if not picked:
        return 0

    ids = [p[0] for p in picked]
    titles = [con.execute("SELECT title FROM items WHERE id=?", (i,)).fetchone()[0] for i in ids]
    labels = _llm_labels(titles)

    n = 0
    for k, (_id, kw) in enumerate(picked):
        beat = labels[k] if labels and labels[k] else kw
        con.execute("UPDATE items SET beat=?, beat_src='llm' WHERE id=?", (beat, _id))
        n += 1
    con.commit()
    return n


HOT_Z = 4.0               # 비트 내 이례적 수준이면 쿼터 무시 (우선 레인)
FLOOR_Z = 0.8             # 균형 레인에 오르려면 자기 비트 평균 이상이어야 한다
_STATS = {"at": 0.0, "d": {}}


def beat_stats(con, ttl=1800):
    """비트별 점수 평균·표준편차 (최근 7일 수집분).

    분쟁은 구조상 점수가 20~30까지 오르고 테크는 15가 상한이라 원점수로 비교하면 불공정하다.
    → 자기 비트 안에서 얼마나 튀는지(z)로 '중요도'를 잰다.
    """
    if _STATS["d"] and time.time() - _STATS["at"] < ttl:
        return _STATS["d"]
    rows = con.execute("""SELECT beat, AVG(score), COUNT(*) FROM items
        WHERE beat IS NOT NULL AND beat!='OTHER' AND score IS NOT NULL
        GROUP BY beat""").fetchall()
    d = {}
    for b, mean, n in rows:
        if n < 20:
            continue
        # 표준편차
        var = con.execute("""SELECT AVG((score-?)*(score-?)) FROM items
            WHERE beat=? AND beat IS NOT NULL AND score IS NOT NULL""",
                          (mean, mean, b)).fetchone()[0] or 1.0
        sd = max(1.5, var ** 0.5)
        d[b] = (mean, sd)
    _STATS["at"], _STATS["d"] = time.time(), d
    return d


def z_score(score, beat, stats):
    mean, sd = stats.get(beat, (9.0, 3.5))
    return (score - mean) / sd


HOT_SCORE = 20.0          # 이 점수 이상은 쿼터를 무시한다 (중요도 우선)
MAX_PENALTY = 6.0


def quota_penalty(con, window=WINDOW):
    """이미 많이 나간 비트에 매기는 감점. 중요도가 쿼터에 밀리지 않게 하는 장치.

    유효점수 = score - penalty.  단 score >= HOT_SCORE 면 penalty 0.
    - 25점 분쟁 vs 12점 테크 → 25 - 6 = 19 > 12  (중요한 뉴스가 이긴다)
    - 15점 분쟁(이미 4건) vs 15점 테크 → 15-4 vs 15 (다양성이 이긴다)
    """
    rows = con.execute("""SELECT beat, COUNT(*) FROM (SELECT beat FROM items
        WHERE posted_at IS NOT NULL ORDER BY posted_at DESC LIMIT ?) GROUP BY beat""",
                       (window,)).fetchall()
    have = {b: c for b, c in rows}
    pen = {}
    for b in BEATS:
        surplus = max(0.0, have.get(b, 0) - TARGET[b])
        pen[b] = min(MAX_PENALTY, 2.0 * surplus)
    return pen


def beat_counts(con, window=5):
    """최근 N건의 비트별 게시 수 (우선 레인 폭주 방지용)."""
    rows = con.execute("""SELECT beat, COUNT(*) FROM (SELECT beat FROM items
        WHERE posted_at IS NOT NULL ORDER BY posted_at DESC LIMIT ?) GROUP BY beat""",
                       (window,)).fetchall()
    return {b: c for b, c in rows}


def beat_deficits(con, window=WINDOW):
    """최근 게시물의 비트 분포 대비 목표 비율의 부족분. 큰 순서로 정렬된 리스트."""
    rows = con.execute("""SELECT beat, COUNT(*) FROM (SELECT beat FROM items
        WHERE posted_at IS NOT NULL ORDER BY posted_at DESC LIMIT ?) GROUP BY beat""",
                       (window,)).fetchall()
    have = {b: c for b, c in rows}
    total = sum(have.values()) or 0
    out = []
    for b in BEATS:
        want = TARGET[b] * (total / 10.0)
        out.append((b, want - have.get(b, 0)))
    out.sort(key=lambda x: -x[1])
    return out


if __name__ == "__main__":
    import sqlite3
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "/var/minis/shared/osint/osint.db"
    con = sqlite3.connect(db)
    print("분류:", classify_pending(con), "건")
    print("부족분:", beat_deficits(con))
