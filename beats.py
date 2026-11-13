#!/usr/bin/env python3
"""beats.py — 기사 비트 분류 + 균형 배분 (2026-11-07)

문제: 키워드 점수만으로 뽑으면 점수 인플레이션 때문에 한 분야(분쟁)가 채널을 독점한다.
해법: ① 제목을 6개 비트로 분류(LLM 배치 + 키워드 폴백) ② 최근 게시물의 비트 분포를 보고
     목표 비율에서 가장 모자란 비트를 먼저 채운다.

비트: TECH / ECONOMY / CONFLICT / POLITICS (+OTHER: 게시 제외)
      2026-11-10 사용자 요청: 금융(FINANCE)·원자재(COMMODITY)·경제(ECONOMY)를 ECONOMY 로 합쳤다.
      화면 칩은 전체/테크/경제/분쟁/정치/기타. 예전 라벨은 norm_beat() 로 흡수한다.
"""
import json
import os
import re
import time
import urllib.request

BEATS = ["TECH", "ECONOMY", "CONFLICT", "POLITICS", "OTHER"]
# OTHER(기타)도 게시한다 — "명확히 분류되지 않는 국제 뉴스"이지 쓰레기 통이 아니다.
# 2026-11-12: ECONOMY 3→2, OTHER 2→1. 실측 오염률이 ECONOMY 60%·OTHER 45%(CONFLICT 6%)라
# 쿼터가 곧 오염 배분기였다. 이 값은 '채울 순서'만 정하므로 낮춰도 분쟁이 독점하지 않는다
# (z 점수 정규화가 이미 비트 간 공정성을 맡는다).
TARGET = {"TECH": 2, "ECONOMY": 2, "CONFLICT": 2, "POLITICS": 1, "OTHER": 1}  # 10건 기준

# ── 형식(form) 분류 (2026-11-12) ─────────────────────────────────────────
# 왜: 👎 11건을 읽어 보니 싫은 이유가 소재·지역이 아니라 **글의 형식**이었다.
#     일상 시황 / 보도자료 / 상황보고 / 전망-only / 지방 정치.
#     그런데 분류 프롬프트는 비트·지역만 물어서 그 판단을 담을 곳이 없었다.
#     → 같은 LLM 콜에서 형식과 유용도(0-10)를 같이 받는다(추가 호출 0).
# 형식 코드
FORMS = ("NEWS", "MARKET", "PROMO", "FORECAST", "LOCAL", "REPORT", "LIFE")
FORM_KO = {"NEWS": "사건", "MARKET": "시황", "PROMO": "홍보", "FORECAST": "전망",
           "LOCAL": "지방", "REPORT": "상황보고", "LIFE": "생활"}
# 형식별 유용도 하한 — 형식마다 '통과할 만한 이유'가 다르다.
# 값은 2026-11-12 실측(게시 211건 재판정)으로 골랐다: 차단 29%, 이 유형이 실제로
# 비취향이던 것 51/81 포착, 손실은 11건(그중 실제 손실은 2건 — 나머지는 IPO 밸류에이션·
# 오피니언으로 사용자가 싫어한 유형). MARKET 8 은 '사실상 항상 버림'이다 —
# 진짜 시장 사건은 모델이 NEWS 로 태깅한다. LOCAL 3·REPORT 3 은 낮게 뒀다:
# 높이면 이스라엘 의회·콩고 국민투표·ISW 전황보고 같은 실제 뉴스가 함께 죽는다.
FORM_MIN = {"NEWS": 2, "MARKET": 8, "PROMO": 6, "FORECAST": 5, "LOCAL": 4,
            "REPORT": 3, "LIFE": 5}
FORM_MIN_UTILITY = 4          # 형식 미상일 때 쓰는 기본값(하위 호환)


def drop_form(form, util, min_utility=None):
    """형식 판정으로 버릴 기사인가 (2026-11-12).

    유용도를 못 받았으면(구형 3필드 응답·LLM 실패) 판단하지 않는다 — 그때는
    정규식 백스톱이 잡는다. 애매하면 남기는 쪽이 안전하다(좋은 기사를 버리는 비용이 더 크다).
    """
    if util is None:
        return False
    try:
        u = int(util)
    except (TypeError, ValueError):
        return False
    f = (form or "NEWS").upper()
    if f not in FORM_MIN:
        f = "NEWS"
    thr = FORM_MIN[f] if min_utility is None else min_utility
    return u < thr

# 소스 카테고리·예전 상태값을 새 비트로 흡수한다.
LEGACY = {"FINANCE": "ECONOMY", "COMMODITY": "ECONOMY", "MARKETS": "ECONOMY"}

def norm_beat(b):
    b = (b or "").strip().upper()
    b = LEGACY.get(b, b)
    return b if b in BEATS else "OTHER"
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
    "POLITICS": ["election", "vote", "parliament", "prime minister", "coalition",
                 "referendum", "opposition", "legislation", "congress", "senate",
                 "cabinet", "impeach", "ballot", "policy", "border", "immigration",
                 "protest"],
    "OTHER": ["summit", "diplomat", "diplomatic", "foreign minister", "treaty", "talks",
              "nato", "united nations", "g7", "g20", "brics", "embassy", "ambassador",
              "state visit", "celebrity", "football", "soccer", "cricket", "world cup", "premier league",
              "k-pop", "box office", "taylor swift", "nba", "nfl", "olympic", "tennis",
              "golf", "fashion", "reality show", "award", "horoscope", "recipe",
              "royal family", "gossip", "obituar", "lottery", "weather forecast",
              "macklemore", "wedding", "chef", "restaurant review"],
}

# 금융·원자재 키워드는 경제로 흡수 (2026-11-10 병합)
KW["ECONOMY"] = KW["ECONOMY"] + KW.pop("FINANCE") + KW.pop("COMMODITY")

# 키워드 오분류 방지 (먼저 적용)
OVERRIDES = [
    (r"west bank", "CONFLICT"), (r"\bgaza\b", "CONFLICT"), (r"\bgolan\b", "CONFLICT"),
    (r"\bcentral bank\b", "ECONOMY"), (r"\bbank of (japan|england|korea)\b", "ECONOMY"),
    (r"\bworld bank\b", "ECONOMY"), (r"\bcentral banking\b", "ECONOMY"),
    (r"\bai\b.{0,12}\bchip", "TECH"), (r"data cent(er|re)", "TECH"),
    (r"\bopec\b", "ECONOMY"), (r"\bcrude oil\b", "ECONOMY"),
    (r"\bpresident trump\b.{0,30}tariff", "ECONOMY"),
    (r"\b(election|parliament|ballot|referendum|congress|senate)\b", "POLITICS"),
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


CAT_HINT = {"tech": "TECH", "finance": "ECONOMY", "conflict": "CONFLICT",
            "economy": "ECONOMY", "commodity": "ECONOMY", "business": "ECONOMY",
            "politics": "POLITICS"}   # 모르는 카테고리는 OTHER


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
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, top = ranked[0]
        # 1점짜리 동률 → "명확히 분류되지 않은 뉴스" (2026-11-10)
        # 단일 키워드로 한 비트만 잡히면 그 비트를 유지한다(예: election → POLITICS)
        if top <= 1 and len(ranked) >= 2 and ranked[1][1] == top:
            return "OTHER"
        if best == "OTHER" and top < 2 and len(ranked) > 1:
            return ranked[1][0]
        return best
    return CAT_HINT.get(cat, "OTHER")   # 단서가 없으면 정치가 아니라 기타


# ── LLM 배치 분류 ────────────────────────────────────────────────
PROMPT = """다음 뉴스 제목을 분류해라. 설명 없이 "번호:비트:지역:형식:유용도" 형식으로만 한 줄씩 답한다.
예) 1:TECH:ASIA:NEWS:8   2:ECONOMY:GLOBAL:MARKET:2   3:OTHER:EUROPE:PROMO:1

비트:
- TECH: 기술·AI·반도체·플랫폼·사이버·우주
- ECONOMY: 경제 전체 — 금융·증시·금리·물가·무역·기업 실적·M&A·원자재·에너지·노동
- CONFLICT: 전쟁·무력 충돌·공습·테러·안보 위기·군사 작전
- POLITICS: **선거·의회·정당·내각 인선·국내 정치** (좁게 잡는다)
- OTHER: 위 넷에 **명확히** 안 들어가는 뉴스. 정상회의·외교 접촉·인물 동향·국제기구·사회·문화·재난·사법 등.

판단 기준: 확신이 안 서면 OTHER 로 보낸다. 예를 들어 "카타르 총리, 파리 G7 회의서 이란 위기 논의"는
선거·의회가 아니라 외교 일정이므로 POLITICS 가 아니라 OTHER 다.
경제와 기술이 겹치면 주된 축으로 고른다(반도체 실적 전망 → TECH, 유가·금리·물가 → ECONOMY).

지역: EUROPE(유럽·러시아 서부), LATAM(중남미·카리브), US(미국·캐나다), ASIA(동아시아·남아시아·동남아·중앙아·오세아니아), AFRICA(사하라 이남 아프리카), MIDDLE_EAST(중동·북아프리카·이란·이스라엘), GLOBAL(여러 지역에 걸친 사안이거나 본문의 주체가 특정 국가가 아님)
사건이 일어난 곳이 아니라 **기사의 주체** 기준으로 고른다.

형식 — **무엇에 관한 기사인가가 아니라 어떤 종류의 글인가**를 본다. 주제가 경제여도 시황이면 MARKET 이다.
- NEWS: 사건·발표·결정·조치가 있다. (공습, 제재, 금리 결정, 인선, 사고, 기업 인수, 규제 발표)
- MARKET: 일상적 시세·지수 등락·개별 종목 등락·종목 추천·"stock market today"·시황 중계·수익률 표.
- PROMO: 보도자료 — 행사 개최/확장, 수상, MOU, 기념식, 기관 홍보, 신제품 광고.
- FORECAST: 사건 없이 전망·예측·"~할 수 있다"·"~할까?"·애널리스트 의견만 있는 글.
- LOCAL: 한 나라 **안에서만** 통하는 정치·선거·인사·의례·연설·지역 행사.
  국제적 파급이 뚜렷하지 않은 내수 정치 기사는 규모와 무관하게 LOCAL 이다.
  (예: 나이지리아 상원의원 경선, 미국 카운티 선거, 지방정부 발표)
- REPORT: 인도지원 상황보고·통계 보고서·"Update #12"·주간 집계.
- LIFE: 종교·연예·스포츠·생활·문화.

유용도 0-10 — 기준은 **국제적 관련성**이다. "중요한가"가 아니라 "다른 나라 독자가 읽을 이유가 있는가".
- 9-10 체계적: 여러 나라의 관계·시장·안보 구도가 바뀐다 (전쟁·제재·금리 결정·대형 인수)
- 7-8 중요: 한 나라의 **국가 수준** 결정(정부·의회·중앙은행·대기업)인데 다른 나라도 주목한다
- 5-6 참고: 업계·지역 단위의 구체적 사실. 그 나라 밖에서는 참고 정도
- 3-4 낮음: **그 나라 안에서만 통하는** 정치·행정·사법·인사·의례, 일상 시황, 예상된 결과,
  근거 약한 전망, 지방 선거·시의회·카운티·주 단위 당무
- 0-2 소음: 홍보, 소문, 선정적 예측, 통계·목록 나열, 조언·칼럼
**형식이 MARKET·PROMO·REPORT·LIFE 이면 유용도는 3 이하로 준다** (단, 시장 붕괴·금리 결정·
제재처럼 사건이면 NEWS 다). 형식을 먼저 정하고 유용도는 그 형식 안에서 매긴다.
**주·시·카운티·지방법원·지방선거는 규모와 무관하게 LOCAL 이고 유용도 3 이하다.**
국가 단위 정치(대통령·의회·중앙은행·외교)만 NEWS 로 두고, 그중에서도 다른 나라에
영향이 없으면 7을 넘기지 않는다.

{titles}"""


def _post(url, payload, headers, timeout=30):   # 5분 주기 안에 끝나야 한다 (2026-11-08: 90s×폴백이 회차를 5분 이상 물고 있었다)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


GEMINI_CLASSIFY_MODELS = ["gemini-flash-lite-latest", "gemini-3.1-flash-lite",
                          "gemini-3-flash-preview"]


def _call_text(prompt, max_tokens=2000, timeout=20):
    """분류용 LLM 1회 호출 — Groq → Gemini → NVIDIA. 실패하면 "".

    무료 한도가 좁다(Groq 200k 토큰/일). 그래서 호출부가 주기를 조절하고,
    같은 기사를 두 번 분류하지 않도록 run_once 가 판정을 state 에 남긴다.
    """
    # Gemini 를 먼저 쓴다. 분류는 **요청 수** 기준 한도인 flash-lite 계열이 남아 있고,
    # Groq 의 하루 토큰(200k)은 기사 요약이 거의 다 먹는다(2026-11-12 실측:
    # 분류가 429로 죽어 형식 게이트가 통째로 꺼졌다). 무료 한도는 모델마다 따로 센다.
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        for model in GEMINI_CLASSIFY_MODELS:
            try:
                r = _post("https://generativelanguage.googleapis.com/v1beta/models/"
                          f"{model}:generateContent?key=" + key,
                          {"contents": [{"parts": [{"text": prompt}]}],
                           "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}},
                          {}, timeout=timeout)
                out = ((r.get("candidates") or [{}])[0].get("content", {})
                       .get("parts") or [{}])[0].get("text", "").strip()
                if out:
                    return out
            except Exception as e:
                msg = str(e)
                if "429" not in msg and "404" not in msg:
                    print(f"[beats] gemini/{model} {type(e).__name__}: {msg[:80]}", flush=True)

    key = os.environ.get("GROQ_API_KEY", "").strip()
    if key:
        try:
            r = _post("https://api.groq.com/openai/v1/chat/completions",
                      {"model": "openai/gpt-oss-120b", "temperature": 0,
                       "max_tokens": max_tokens, "reasoning_effort": "low",
                       "messages": [{"role": "user", "content": prompt}]},
                      {"Authorization": f"Bearer {key}"}, timeout=timeout)
            out = (r["choices"][0]["message"].get("content") or "").strip()
            if out:
                return out
        except Exception as e:
            print(f"[beats] groq {type(e).__name__}: {str(e)[:100]}", flush=True)

    # 3) NVIDIA NIM — Gemini/Groq 가 동시에 429/503 인 날이 잦다(2026-11-10 실측).
    try:
        import llm
        out = llm.call_nvidia(os.environ.get("NVIDIA_API_KEY", "").strip(), prompt)
        if out:
            return out.strip()
    except Exception as e:
        print(f"[beats] nvidia {type(e).__name__}: {str(e)[:100]}", flush=True)
    return ""


def _llm_labels(titles):
    """제목 리스트 → [(beat, region, form, util)]. 실패하면 None."""
    body_text = "\n".join(f"{i+1}. {t[:110]}" for i, t in enumerate(titles))
    out = _call_text(PROMPT.format(titles=body_text), max_tokens=2000)
    return _parse(out, len(titles)) if out else None


# 한 건짜리 분류 — 고른 후보의 형식이 비어 있을 때만 부른다(pick 안에서).
# 배치 프롬프트의 루브릭이 600토큰이라 5분마다 부르면 하루 한도를 넘는다.
# 이 프롬프트는 ~130토큰이다.
ONE_PROMPT = """제목 하나를 분류해라. "비트:지역:형식:유용도" 한 줄만 답한다.
비트: TECH ECONOMY CONFLICT POLITICS OTHER
지역: EUROPE LATAM US ASIA AFRICA MIDDLE_EAST GLOBAL
형식: NEWS(사건·결정·조치) MARKET(일상 시세·지수·종목 등락) PROMO(보도자료·행사·수상)
     FORECAST(사건 없이 전망만) LOCAL(지방 정치·의례·연설) REPORT(상황보고·통계) LIFE(종교·연예·스포츠·생활)
유용도 0-10 — 기준은 국제적 관련성(다른 나라 독자가 읽을 이유).
9-10 여러 나라 구도가 바뀐다 / 7-8 국가 수준 결정인데 다른 나라도 주목 / 5-6 업계·지역 사실
/ 3-4 그 나라 안에서만 통하는 정치·사법·인사·지방선거·일상 시황 / 0-2 홍보·소문·조언·목록
MARKET·PROMO·REPORT·LIFE 형식이면 3 이하. 주·시·카운티·지방선거는 LOCAL, 3 이하.
(시장 붕괴·금리 결정·제재 같은 사건은 NEWS)
제목: {title}"""


def classify_one(title):
    """제목 1건 분류. 실패하면 None(호출부는 정규식 백스톱으로 넘어간다)."""
    out = _call_text(ONE_PROMPT.format(title=(title or "")[:130]), max_tokens=24, timeout=12)
    if not out:
        return None
    # 모델은 "ECONOMY:US:MARKET:2" 처럼 번호 없이 답한다 — 파서는 번호를 요구한다.
    labs = _parse("1:" + out.strip().splitlines()[0], 1)
    return labs[0] if labs else None


_BIT_RE = re.compile(r"(TECH|FINANCE|CONFLICT|COMMODITY|ECONOMY|POLITICS|OTHER)", re.I)

def _bit(s):
    """LLM 이 옛 라벨(FINANCE/COMMODITY)을 뱉어도 ECONOMY 로 흡수한다."""
    return norm_beat(s)
LLM_REGIONS = ("EUROPE", "LATAM", "US", "ASIA", "AFRICA", "MIDDLE_EAST", "GLOBAL")


def _parse(text, n):
    """'번호:비트:지역[:형식[:유용도]]' → [(beat, region, form, util)].

    지역이 없으면 None 으로 두고 호출부에서 키워드 분류(kw_region)로 메운다.
    (2026-11-08: 비트만 물어보던 것을 같은 콜에서 지역까지 받도록 확장 — GLOBAL 43% 대응)
    2026-11-12: 형식·유용도 추가. **3필드 응답도 그대로 받는다**(form/util=None) —
    모델이 옛 형식으로 답하거나 프롬프트를 일부만 따를 때 분류 자체가 죽으면 안 된다.
    """
    labels = [None] * n
    for line in text.splitlines():
        m = (re.match(r"\s*(\d+)\s*[:.\)]\s*(.+)", line.strip())
             or re.match(r"\s*(\d+)\s+(.+)", line.strip()))
        if not m:
            continue
        i = int(m.group(1)) - 1
        if not (0 <= i < n):
            continue
        rest = m.group(2)
        # 순서를 가정하지 않고 필드 성질로 집어낸다(모델이 순서·구두점을 바꿔도 살린다).
        beat = region = form = util = None
        for f in re.split(r"[:|;,]", rest):
            for g in f.replace("(", " ").replace(")", " ").split():
                g = g.strip(".").upper()
                if not g:
                    continue
                if beat is None and _BIT_RE.fullmatch(g):
                    beat = _bit(g)
                elif region is None and g in LLM_REGIONS:
                    region = g
                elif form is None and g in FORMS:
                    form = g
                elif util is None and re.fullmatch(r"\d{1,2}", g):
                    util = min(10, int(g))
        if beat is None:
            bm = _BIT_RE.search(rest)                     # 마지막 폴백: 문장 안의 비트 단어
            if not bm:
                continue
            beat = _bit(bm.group(1))
        labels[i] = (beat, region, form, util)
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
        # OTHER 도 후보로 넣는다 — LLM 이 더 정확한 비트로 재분류할 기회를 준다
        # (2026-11-10: 예전엔 OTHER 를 여기서 버려서 '기타'가 영원히 비어 있었다)
        b = norm_beat(beat or kw_beat(title, cat))
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
    got_llm = [0]
    for k, (_id, kw) in enumerate(picked):
        lab = labels[k] if labels and labels[k] else None
        beat = (lab[0] if lab else None) or kw
        region = (lab[1] if lab else None) or kw_region(titles[k])
        form = lab[2] if lab else None
        util = lab[3] if lab else None
        # ── LLM 보정 (2026-11-11 실측: "나이지리아 경찰청장 테러 피해 지역 병력 배치"가
        #    OTHER/GLOBAL 로 분류돼 '국제·기타'로 나갔다) ─────────────────────
        kr = kw_region(titles[k])
        if kr == "AFRICA":
            region = "AFRICA"                # 아프리카 국가명은 확실한 신호 (2026-11-11)
        elif region in (None, "GLOBAL") and kr and kr != "GLOBAL":
            region = kr                      # 나라 이름이 제목에 있으면 그 지역으로
        if beat == "OTHER":
            kwb = kw_beat(titles[k])
            if kwb and kwb != "OTHER":
                beat = kwb                   # '기타'로 보냈는데 키워드가 확실하면 그쪽으로
        # LLM 이 실패한 회차(labels=None)는 beat_src='kw' 로 남겨 **다음 회차에 다시 시도**한다.
        # 예전에는 실패해도 'llm' 으로 찍어서, 429 한 번에 그 기사가 영원히
        # 형식·유용도 없이(→ 형식 게이트 밖에서) 게시됐다 (2026-11-12 드라이런에서 발견).
        src = "llm" if lab else "kw"
        if src == "kw" and form is not None:
            src = "llm"
        con.execute("""UPDATE items SET beat=?, beat_src=?, region=?, form=?, util=?
                       WHERE id=?""", (beat, src, region, form, util, _id))
        if form is not None:
            got_llm[0] += 1
        n += 1
    con.commit()
    return n, got_llm[0]


def form_stats(con, days=2):
    """형식 분포 — 게이트가 실제로 무엇을 버리는지 보는 창(운영 점검용)."""
    rows = con.execute("""SELECT COALESCE(NULLIF(form,''),'(미분류)'), COUNT(*),
            SUM(CASE WHEN note='form' OR note LIKE 'form:%' THEN 1 ELSE 0 END)
        FROM items GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    for f, n, blocked in rows:
        print(f"  {f:<9} {n:>5}건  차단 {blocked or 0}")


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
    n_cls, n_llm = classify_pending(con)
    print(f"분류: {n_cls}건 (LLM {n_llm})")
    print("부족분:", beat_deficits(con))
