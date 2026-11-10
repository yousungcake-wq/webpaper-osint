#!/usr/bin/env python3
"""osint.py — 상시 OSINT 수집/보도 엔진 (Leyley)

서브커맨드
  collect    소스 전체 수집 → DB 저장 (1시간 주기)
  tick       10분 주기: 가장 중요한 미보도 기사 1건을 채널에 전송
  daemon     collect + tick 무한 루프 (nohup 으로 실행)
  status     현재 상태 요약
  audit      주간 점검 리포트 생성 (--send 로 개인 DM 전송)

DB: /var/minis/shared/osint/osint.db
설정: /var/minis/shared/osint/config.json
"""
import argparse, hashlib, json, os, re, sqlite3, subprocess, sys, time
import datetime as dt
import urllib.parse, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))   # 저장소 어디에 있든 그 폴더 기준
DB = f"{BASE}/osint.db"
CONF = f"{BASE}/config.json"
NEWSDIR = BASE
sys.path.insert(0, NEWSDIR)
import news_fetch as nf  # noqa: E402
import filters as flt  # noqa: E402

DEFAULT_CONF = {
    "channel": "@webpaperHQ",
    "owner_chat": "",
    "min_score": 19.0,
    "hot_score": 30.0,
    "max_age_hours": 6,
    "collect_every_s": 3600,
    "post_every_s": 600,
    "max_posts_per_hour": 6,
    "quiet_empty": True,
}


def conf():
    c = dict(DEFAULT_CONF)
    if os.path.exists(CONF):
        try:
            c.update(json.load(open(CONF, encoding="utf-8")))
        except Exception:
            pass
    # 레포는 public 이라 개인 채팅 ID 를 config.json 에 두지 않는다. Actions 에서는
    # TG_OWNER_CHAT 시크릿으로 주입한다. 이게 없으면 알림이 공개 채널로 새므로,
    # 비어 있을 때 notify 는 채널로 보내지 않고 조용히 실패한다(2026-11-10).
    env_chat = os.environ.get("TG_OWNER_CHAT", "").strip()
    if env_chat:
        c["owner_chat"] = env_chat
    return c


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def kst(d: dt.datetime) -> str:
    return (d + dt.timedelta(hours=9)).strftime("%H:%M")


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE, title TEXT, descr TEXT, url TEXT,
        feed TEXT, cat TEXT, ts TEXT, first_seen TEXT,
        score REAL, posted INTEGER DEFAULT 0, posted_at TEXT, note TEXT,
        beat TEXT, region TEXT, cu TEXT, summary TEXT)""")
    for col in ("cu", "summary"):                 # 기존 DB 마이그레이션
        try:
            con.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS tr(key TEXT PRIMARY KEY, ko TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS gnurl(gn TEXT PRIMARY KEY, real TEXT, at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT,
        ok INTEGER, items INTEGER, errors TEXT)""")
    return con


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, str(v)))


LOG = f"{BASE}/daemon.log"


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ── 번역 ──────────────────────────────────────────────────────────────
def translate(text: str, con=None) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if not re.search(r"[A-Za-z]{3}", text):
        return text
    if con is not None:
        r = con.execute("SELECT ko FROM tr WHERE key=?", (text,)).fetchone()
        if r:
            return r[0]
    q = urllib.parse.quote(text[:900])
    url = ("https://translate.googleapis.com/translate_a/single"
           f"?client=gtx&sl=en&tl=ko&dt=t&q={q}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        out = "".join(seg[0] for seg in data[0] if seg and seg[0])
        out = re.sub(r"\s+", " ", out).strip()
        if con is not None and out:
            con.execute("INSERT OR REPLACE INTO tr(key,ko) VALUES(?,?)", (text, out))
            con.commit()
        return out
    except Exception:
        return text


# ── 수집 ──────────────────────────────────────────────────────────────
CAT_W = {"conflict": 5.0, "politics": 4.0, "economy": 4.0,
         "finance": 4.0, "tech": 4.0, "google": 1.5}

# 지역·주제 가중 (2026-11-07 Gram: 국제분쟁, 특히 아프리카·중동의 저보도 분쟁)
THEATER_WORDS = {
    # 아프리카 (잘 다뤄지지 않는 곳 — 가중 높게)
    "sudan": 4, "darfur": 4, "rsf": 4, "sahel": 4, "mali": 3, "niger": 3,
    "burkina": 3, "chad": 3, "congo": 3, "m23": 4, "goma": 3, "kivu": 3,
    "ethiopia": 3, "tigray": 3, "amhara": 3, "somalia": 3, "somaliland": 3,
    "south sudan": 3, "central african republic": 3, "mozambique": 3, "cabo delgado": 3,
    "nigeria": 2, "cameroon": 2, "haiti": 3, "libya": 3,
    # 중동
    "yemen": 3, "houthi": 3, "gaza": 3, "hezbollah": 3, "lebanon": 2,
    "syria": 2, "iraq": 2, "iran": 3, "israel": 1, "west bank": 2, "hormuz": 3,
}
HUMAN_WORDS = {
    "famine": 3, "displacement": 2, "displaced": 2, "refugee": 2, "massacre": 3,
    "atrocit": 3, "genocide": 3, "militia": 2, "junta": 3, "coup": 3,
    "insurgen": 3, "child soldiers": 3, "ethnic cleansing": 3, "aid convoy": 2,
    "humanitarian": 2, "ceasefire": 3, "peace talks": 2, "war crime": 3,
}

TECH_WORDS = {
    "ai": 3, "artificial intelligence": 3, "openai": 3, "nvidia": 3, "semiconductor": 3,
    "chip": 3, "chips": 3, "tsmc": 3, "data center": 2, "cloud": 2, "model": 1,
    "startup": 1, "antitrust": 3, "regulation": 2, "privacy": 2, "cyber": 2,
    "quantum": 2, "robot": 2, "space": 2, "apple": 2, "microsoft": 2, "google": 2,
    "meta": 2, "amazon": 2, "tesla": 2, "ipo": 2,
}
FIN_WORDS = {
    "market": 2, "stocks": 2, "shares": 2, "yield": 3, "bond": 2, "treasury": 2,
    "nasdaq": 3, "s&p": 3, "dollar": 2, "euro": 1, "currency": 2, "fed": 3,
    "ecb": 3, "central bank": 3, "interest rate": 3, "rate cut": 3, "inflation": 3,
    "gdp": 3, "recession": 3, "earnings": 2, "merger": 3, "acquisition": 3,
    "ipo": 3, "private equity": 2, "hedge fund": 2, "credit": 2, "default": 3,
    "bank": 2, "imf": 2, "bailout": 3, "tariff": 3, "trade deal": 3, "oil price": 2,
}
POL_WORDS = {
    "election": 3, "summit": 3, "sanctions": 3, "treaty": 3, "diplomat": 3,
    "parliament": 2, "president": 1, "prime minister": 2, "coalition": 2,
    "opposition": 2, "referendum": 3, "coup": 3, "border": 2, "taiwan": 3,
    "nato": 3, "eu": 2, "brics": 3, "g7": 2, "g20": 2, "un ": 1,
}
WAR_WORDS = {
    "ceasefire": 3, "strike": 1, "strikes": 1, "missile": 1, "drone": 1, "killed": 1,
    "attack": 1, "troops": 1, "offensive": 1, "invasion": 2,
    # 확전·군사행동 신호 (2026-11-07 보강)
    "airstrike": 3, "air strike": 3, "nuclear site": 3, "nuclear program": 2,
    "escalat": 3, "retaliat": 3, "war": 2, "bomb": 2, "blockade": 2,
    "mobiliz": 2, "offensive": 2, "military operation": 2, "truce": 2,
    "evacuat": 2, "crisis": 1,
}
LOW_VALUE = re.compile(
    r"(?i)^(commentary|opinion|analysis|explainer|editorial|column|review|"
    r"\[market rider\]|what to know|here.s what|explained|"
    r"market outlook|listicle|weekly wrap|daily brief|morning brief)")

LOW_VALUE_ANY = re.compile(
    r"(?i)(\d+\s+ways|ways to|things to know|things you|what you need to know|"
    r"tips (for|to)|guide to|everything you|here are the)")

LOCAL_MARKET = re.compile(
    r"(?i)(naira|peso|rupee|rand|shilling|cedi|dong|ringgit|baht)\b")

# 시세·시황 나열 기사 (예: "Gold, Silver Rates Today (November 8): Check Daily")
PRICE_LIST = re.compile(
    r"(?i)(rates? today|price today|today.s (gold|silver|petrol|diesel)|"
    r"check (daily|today)|gold rate|silver rate|petrol price|diesel price|"
    r"stock (tips|picks)|top gainers|top losers|multibagger|sensex|nifty|"
    r"samvat|buy or sell|should you buy|target price|stocks to (watch|buy))")

# 저신호 외교 예의 기사 (조의·축하·생일). 전쟁 어휘가 제목에 있어 점수가 부풀지만
# 국제정치적으로 읽을거리가 없다. (2026-11-08 이관 테스트: '정상이 부친상 조의' 33점 1위)
COURTESY = re.compile(
    r"(?i)(condol|mourn|tribute|sympath|congratulat|felicitat|birthday|"
    r"greets? |extends? (greeting|wish)|anniversary of independence)")

NOISE = ["celebrity", "football", "soccer", "cricket", "horoscope", "recipe",
         "royal family", "gossip", "obituar", "lottery", "weather forecast",
         "world cup", "premier league", "champions league", "box office",
         "k-pop", "taylor swift", "nba", "nfl", "olympic", "tennis", "golf",
         "fashion week", "reality show", "award show"]


_FB_CACHE = {}


def fb_adj(feed="", beat=""):
    """주간 점검 반응(👎/👍)에서 나온 점수 보정. feedback.py apply 가 config.json 에 쓴다.

    값은 feedback.compute() 가 표본 수로 가중해 만든다(소스별 -4~+2). 비트 보정은
    2026-11-10 없앴지만 옛 config 를 읽을 수 있게 키는 남겨 둔다. 창(기본 21일)을
    벗어난 반응은 사라지므로 보정도 같이 옛 상태로 돌아간다. 읽기 실패는 0.
    """
    try:
        mt = os.path.getmtime(os.path.join(NEWSDIR, "config.json"))
    except OSError:
        mt = 0
    fb = _FB_CACHE.get("v") if _FB_CACHE.get("mt") == mt else None
    if fb is None:
        _FB_CACHE["mt"] = mt
        try:
            fb = conf().get("feedback") or {}
        except Exception:
            fb = {}
        _FB_CACHE["v"] = fb
    if not fb:
        return 0.0
    v = (fb.get("src", {}).get(feed, 0.0) or 0.0) + \
        (fb.get("beat", {}).get(beat, 0.0) or 0.0)
    return max(-5.0, min(2.5, v))        # 소스+비트가 겹쳐도 총합은 이 범위


def score_item(it, cat, now):
    """관심사(국제정치·테크·금융·경제) 중심 채점."""
    txt = (it["title"] + " " + it.get("desc", "")).lower()
    pts = CAT_W.get(cat, 1.0)
    for table, mult in ((TECH_WORDS, 1.0), (FIN_WORDS, 1.0), (POL_WORDS, 1.0)):
        for w, wt in table.items():
            if w in txt:
                pts += wt * mult
    for w, wt in WAR_WORDS.items():
        if w in txt:
            pts += wt
    for w, wt in THEATER_WORDS.items():
        if w in txt:
            pts += wt
    for w, wt in HUMAN_WORDS.items():
        if w in txt:
            pts += wt
    pts += nf.PRIORITY.get(it.get("feed", ""), 1)
    age_h = ((now - it["ts"]).total_seconds() / 3600) if it.get("ts") else 4
    pts += max(0.0, 4.0 * (1 - age_h / 4))          # 최근성
    for n in NOISE:
        if n in txt:
            pts -= 8
    if LOW_VALUE.search(it["title"]) or LOW_VALUE_ANY.search(it["title"]):
        pts -= 6                                  # 오피니언·리스트 기사
    if LOCAL_MARKET.search(it["title"]):
        pts -= 5                                  # 현지 통화·로컬 증시 위주 기사
    if PRICE_LIST.search(it["title"]):
        pts -= 9                                  # 시세 나열·추천 기사
    if COURTESY.search(it["title"]):
        pts = min(pts, 6.0)                       # 조의·축하 등 저신호 외교 예의 → 하한 미달로 탈락
    return round(pts, 1)


def collect(con, hours=8):
    sources = nf.load_sources(f"{NEWSDIR}/sources.txt")
    now = now_utc()
    cutoff = now - dt.timedelta(hours=hours)
    errors, added, seen = [], 0, 0

    def work(s):
        cat, name, url = s
        try:
            return (s, nf.parse_feed(nf.fetch(url, timeout=12)), None)
        except Exception as e:
            return (s, [], f"{type(e).__name__}: {str(e)[:50]}")

    from concurrent.futures import ThreadPoolExecutor, wait
    results = []
    ex = ThreadPoolExecutor(max_workers=12)
    futs = {ex.submit(work, s): s for s in sources}
    done, not_done = wait(futs, timeout=45)          # 전체 수집에 45초 예산
    for f in done:
        results.append(f.result())
    for f in not_done:
        results.append((futs[f], [], "Timeout: 사이클 예산 초과"))
        f.cancel()
    ex.shutdown(wait=False, cancel_futures=True)     # 남은 스레드는 기다리지 않는다

    if results:
        for (cat, name, url), items, err in results:
            if err:
                errors.append(f"{name}: {err}")
                continue
            for it in items:
                if it["ts"] and it["ts"] < cutoff:
                    continue
                it["src"] = it["orig_source"] or name
                it["cat"] = cat
                it["feed"] = name
                title = it["title"]
                disp = name
                if name.startswith("GN "):
                    disp = it["orig_source"] or name
                    title = re.sub(r"\s+[-–|]\s+[^-–|]{2,45}$", "", title).strip()
                if CONFLICT_WORDS.search(title):
                    cat = "conflict"
                key = hashlib.sha1(nf.norm_title(title)[:90].encode()).hexdigest()[:16]
                # 같은 사건을 다른 매체가 쓴 경우: 점수만 갱신
                # 같은 URL 이 이미 (게시된 것으로) 들어 있으면 같은 기사다.
                # 제목이 살짝 바뀌면 key 가 달라져 중복을 놓친다(2026-11-09: 4시간 20분 간격 재게시).
                # 주소는 정규화해서 본다 — ?utm=, /amp, 끝 슬래시만 다른 같은 글을 잡는다.
                cu = flt.canon_url(it["url"])
                if cu and con.execute(
                        "SELECT 1 FROM items WHERE cu=? AND posted=1", (cu,)).fetchone():
                    continue
                row = con.execute("SELECT id, score FROM items WHERE key=?", (key,)).fetchone()
                sc = score_item(it, cat, now)
                if row:
                    if sc > (row[1] or 0):
                        con.execute("UPDATE items SET score=?, feed=? WHERE id=?",
                                    (sc, name, row[0]))
                    continue
                # 다른 매체의 유사 제목(중복 사건) 억제
                con.execute("""INSERT OR IGNORE INTO items
                    (key,title,descr,url,feed,cat,ts,first_seen,score,cu)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (key, title, it["desc"][:400], it["url"], disp, cat,
                             it["ts"].strftime("%Y-%m-%d %H:%M UTC") if it["ts"] else "",
                             now.strftime("%Y-%m-%d %H:%M UTC"), sc, cu))
                added += 1
                seen += 1
    con.execute("INSERT INTO runs(at,kind,ok,items,errors) VALUES(?,?,?,?,?)",
                (now.strftime("%Y-%m-%d %H:%M UTC"), "collect",
                 0 if errors else 1, added, json.dumps(errors, ensure_ascii=False)))
    meta_set(con, "last_collect", now.strftime("%Y-%m-%d %H:%M UTC"))
    con.commit()
    return added, errors


# ── 선택 ──────────────────────────────────────────────────────────────
STOP = set("the a an of to in on for and as at by with from says say new "
           "over after before amid into its his her their this that".split())


def _stem(w):
    """가벼운 영어 어간 축약. kill/killed·strike/strikes·Houthi/Houthis 를 같은 토큰으로 묶는다.
    (2026-11-08: 이게 없어서 사나 공습이 3번 게시됐다 — 제목 자카드가 0.18~0.21로 낮았다)"""
    if len(w) <= 4:
        return w
    for suf in ("ing", "ed", "ies", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def toks(t):
    return set(_stem(w) for w in re.findall(r"[a-z0-9]{3,}", t.lower()) if w not in STOP)


def similar(a, b):
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


CONFLICT_WORDS = re.compile(
    r"(?i)\b(war|strike|strikes|airstrike|attack|attacked|killed|dead|casualt|"
    r"ceasefire|truce|troops|military|missile|drone|bomb|shelling|offensive|"
    r"invasion|front line|sanctions?|hostage|refugee|clash)\b")

JUNK_TITLE = re.compile(
    r"(?i)(\barchives?\b|home ?page|\bsubscribe\b|\blog ?in\b|sign in|"
    r"\bobituar|horoscope|weather forecast|lottery|\bstock quote|"
    r"stock market analysis|market analysis|\btradingview\b|"
    r"\([A-Z]{1,5}:[A-Z]{2,6}\)|"                     # 티커 표기 (IPO:CA, NVDA:NASDAQ)
    r"^https?://|^\.|\|\s*$)")
JUNK_DOMAIN = ("tvoneusa", "energynow", "journalarta", "montel", "britannica",
               "azernews", "postandcourier", "notus", "timesofindia",
               "finance.biggo.com", "podcast", "mlq.ai", "simplywall.st",
               "investing.com", "marketbeat", "fool.com")

# 종목 추천·애널리스트 목표가·팟캐스트 요약 — 뉴스가 아니라 투자 광고다.
# (2026-11-08 표본: '3 US Bank Stocks Built For Higher Interest Rates', 'Korea Tires target price…')
NOISE_TITLE = re.compile(
    r"(?i)(\b\d+\s+.{0,28}\bstocks?\b|\bstocks? i'?d buy\b|retail investors are watching|"
    r"target price|price target|\bpodcast\b|\bwhat to watch\b|cramer|"
    r"\btranscript\b|here'?s what to know|should investors)")


def junk_item(title, url):
    if not title or len(title) < 22:
        return True
    if JUNK_TITLE.search(title):
        return True
    if not re.search(r"[a-z]{3}", title):
        return True
    if title.count("-") >= 4 and title.count(" ") <= 2:
        return True
    if any(d in (url or "") for d in JUNK_DOMAIN):
        return True
    # 잘린 링크: 쿼리가 "?id" 처럼 값 없이 끝난다 (2026-11-10 👎 #737 FormatBiz)
    if re.search(r"\?[A-Za-z_]{1,6}$", url or "") or re.search(r"[?&][A-Za-z_]{1,20}=$", url or ""):
        return True
    return False


def resolve_url(con, url):
    if "news.google.com" not in url:
        return url
    row = con.execute("SELECT real FROM gnurl WHERE gn=?", (url,)).fetchone()
    if row:
        return row[0]
    import writer
    real = writer.resolve_google(url)
    con.execute("INSERT OR REPLACE INTO gnurl(gn,real,at) VALUES(?,?,?)",
                (url, real, now_utc().strftime("%Y-%m-%d %H:%M UTC")))
    con.commit()
    return real


_DF_CACHE = {"at": 0.0, "df": {}}


def df_map(con, days=7, ttl=600):
    """최근 N일 제목에서 토큰별 등장 문서 수. 희귀 토큰(개체명) 판정에 쓴다.

    5분마다 pick() 이 부르는데 전체 제목 스캔은 낭비라 10분 캐시.
    """
    if _DF_CACHE["df"] and time.time() - _DF_CACHE["at"] < ttl:
        return _DF_CACHE["df"]
    since = (now_utc() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M UTC")
    df = {}
    for (t,) in con.execute("SELECT title FROM items WHERE first_seen >= ?", (since,)):
        for w in toks(t or ""):
            df[w] = df.get(w, 0) + 1
    _DF_CACHE["at"], _DF_CACHE["df"] = time.time(), df
    return df


# 사상자·피해 어휘. 같은 사건이면 대개 양쪽 제목에 함께 나온다.
CASUAL_WORDS = {"kill", "dead", "death", "die", "wound", "injure", "casualt",
                "dozen", "deadli", "toll", "massacr"}


def same_event(a, b, df, n_docs=1800):
    """같은 사건인가. 제목 어휘 유사도 + 희귀 개체명 겹침으로 판정.

    예) 'Tesla to wind down Cybertruck...' vs 'Tesla's Musk pulls plug on Cybertruck...'
        → 자카드 0.19로 놓치지만, 희귀 토큰(tesla·cybertruck·musk) 3개가 겹쳐 잡힌다.
    """
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return False
    inter = ta & tb
    if not inter:
        return False
    union = ta | tb
    jac = len(inter) / len(union)
    if jac > 0.45:
        return True
    # 어휘가 조금 달라도 같은 사건인 경우 (실측 2026-11-08, 게시분 20쌍 전부 실제 중복)
    #   'At least 25 killed in Yemen in Saudi airstrikes' vs 'Airstrikes kill more than 2 dozen near Yemen's capital'
    # 단순 공통토큰 개수만 보면 전장 어휘가 겹치는 다른 사건까지 묶인다
    #   'Houthis claim attacks on Saudi Aramco sites' vs '…attack on Houthi positions in al-Misrakh'
    # → 그래서 (사상자 어휘가 양쪽에 있거나 자카드가 0.30 이상)일 때만 인정한다.
    casual = CASUAL_WORDS & inter
    if len(inter) >= 4 and jac >= 0.20:
        return True
    if len(inter) >= 3 and (casual or jac >= 0.30):
        return True
    rare_cut = max(6, int(n_docs * 0.02))
    rare_shared = [w for w in inter if df.get(w, 0) <= rare_cut]
    return len(rare_shared) >= 2


_FEED_BAD = {"at": 0.0, "set": set()}

DUP_THR = 0.18   # 근거는 dup_body 문서화 주석 참조

def dup_body(con, body, hours=72, thr=DUP_THR):
    """생성된 요약문이 최근 게시분과 같은 사건인가 (교차 매체 중복).

    영문 제목·URL은 매체마다 다르지만 한국어 요약은 거의 같아진다.

    임계값 재측정(2026-11-10, 게시 198건 = 19,503쌍):
      · 0.22 이상: 0쌍 → **한 번도 발화하지 않았다**(직전 값 0.22는 채널 100건 표본에서
        3쌍만 보고 정한 것이라 너무 높았다).
      · 0.18 ~ 0.22: 12쌍, 전부 같은 사건(프랑스 냉장고 해킹 5건 / 예멘 사나 공습 3건 /
        젠슨 황 스파이 4건) — 0.16~0.18 구간도 전부 같은 사건이었다.
      · 0.15 이하: 같은 '사가'의 다른 각도(주가 급락 vs 이사회 조사) — 차단하면 과잉.
    그래서 0.18. 시뮬레이션: 198건 중 7건 차단(3.5%), 전부 실제 중복.
    """
    if not body or len(body) < 40:
        return None
    cutoff = (now_utc() - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M UTC")
    rows = con.execute(
        """SELECT summary FROM items
           WHERE posted=1 AND COALESCE(summary,'')<>'' AND posted_at>=?
           ORDER BY posted_at DESC LIMIT 250""", (cutoff,)).fetchall()
    for (prev,) in rows:
        if flt.overlap(body, prev) >= thr:
            return prev
    return None

def bad_feeds(con, min_attempts=5, ratio=0.65, ttl=600):
    """본문 추출이 계속 실패하는 피드(페이월·JS 페이지)를 뽑는다.

    이런 피드가 후보를 독점하면 게시가 조용히 멈춘다(2026-11-07 45분 공백 사고).
    """
    if _FEED_BAD["set"] and time.time() - _FEED_BAD["at"] < ttl:
        return _FEED_BAD["set"]
    rows = con.execute("""SELECT feed,
            SUM(CASE WHEN note='no_body' THEN 1 ELSE 0 END) nb, COUNT(*) tot
        FROM items WHERE posted=1 AND posted_at IS NULL GROUP BY feed""").fetchall()
    bad = {f for f, nb, tot in rows if tot >= min_attempts and nb / tot >= ratio}
    _FEED_BAD["at"], _FEED_BAD["set"] = time.time(), bad
    return bad


_FEED_HEALTH = {"at": 0.0, "d": {}}


def feed_health(con, ttl=900):
    """피드별 본문 추출 성공률. 낮은 피드는 같은 비트를 계속 막으므로 뒤로 민다."""
    if _FEED_HEALTH["d"] and time.time() - _FEED_HEALTH["at"] < ttl:
        return _FEED_HEALTH["d"]
    rows = con.execute("""SELECT feed,
            SUM(CASE WHEN note='no_body' THEN 1 ELSE 0 END) nb, COUNT(*) tot
        FROM items WHERE posted=1 GROUP BY feed""").fetchall()
    d = {}
    for f, nb, tot in rows:
        d[f] = 1.0 - (nb / tot if tot else 0.0)
    _FEED_HEALTH["at"], _FEED_HEALTH["d"] = time.time(), d
    return d


def _candidate_ok(con, r, df, recent, cutoff_s, min_score):
    """후보 검증(구글뉴스 해석·정크·만료·중복).

    통과하면 **해석된 URL을 반영한 새 튜플**을, 아니면 None을 돌려준다.
    (구글뉴스 링크를 그대로 넘기면 본문 추출이 100% 실패한다 — 2026-11-07 사고)
    """
    _id, title, descr, url, feed, cat, ts, sc = r[:8]
    if url and "news.google.com/rss/articles" in url:
        url = resolve_url(con, url)
        con.execute("UPDATE items SET url=? WHERE id=?", (url, _id))
        r = (r[0], r[1], r[2], url) + tuple(r[4:])
    if not url or "news.google.com/rss/search" in url or junk_item(title, url):
        con.execute("UPDATE items SET posted=1, note='no_direct_url' WHERE id=?", (_id,))
        return None
    if NOISE_TITLE.search(title or ""):
        con.execute("UPDATE items SET posted=1, note='noise' WHERE id=?", (_id,))
        return None
    if ts and ts < cutoff_s:
        con.execute("UPDATE items SET posted=1, note='expired' WHERE id=?", (_id,))
        return None
    if sc + fb_adj(feed, r[8] if len(r) > 8 else "") < min_score:
        return None
    # 라운드업·오피니언·종목추천은 내용이 섞여서 요약이 사고를 낸다.
    # (2026-11-09: 금리 기사 요약에 F1 브라질 우승이 붙었다 — Rio Times Big Read)
    reason = flt.is_digest(title, url)
    if reason:
        con.execute("UPDATE items SET posted=1, note=? WHERE id=?", (reason, _id))
        con.commit()
        log(f"{reason}: {title[:50]}")
        return None
    dup_of = next((t for t in recent if similar(title, t) > 0.45 or same_event(title, t, df)), None)
    if dup_of:
        con.execute("UPDATE items SET posted=1, note='dup' WHERE id=?", (_id,))
        log(f"dup: {title[:46]} ≒ {dup_of[:46]}")   # 과잉 차단 추적용 (2026-11-08)
        return None
    return url        # 실제 기사 URL (구글뉴스 링크면 해석된 주소)


def pick(con, cfg):
    """비트 균형 배분으로 후보 1건 선택 (2026-11-07).

    점수 1등만 뽑으면 한 분야가 채널을 독점한다. 최근 10건의 비트 분포를 보고
    목표 비율에서 가장 모자란 비트부터 채운다. 그 안에서는 점수 순.
    """
    import beats as _b
    cutoff = now_utc() - dt.timedelta(hours=cfg["max_age_hours"])
    cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M UTC")
    recent = [r[0] for r in con.execute(
        "SELECT title FROM items WHERE posted=1 ORDER BY posted_at DESC LIMIT 80")]
    try:
        df = df_map(con)
    except Exception:
        df = {}

    
    sql = """SELECT id,title,descr,url,feed,cat,ts,score,beat,region FROM items
        WHERE posted=0 AND (beat=? OR (COALESCE(beat,'')=? AND cat=?))
        ORDER BY score DESC LIMIT 30"""
    cat_hint = {"TECH": "tech", "FINANCE": "finance", "CONFLICT": "conflict",
                "ECONOMY": "economy", "POLITICS": "politics"}

    bad = bad_feeds(con)
    health = feed_health(con)
    pen = _b.quota_penalty(con)
    stats = _b.beat_stats(con)

    # 비트별 상위 후보를 모아 **전역 유효점수**로 정렬한다.
    # (비트 순서대로 뽑으면 25점 기사가 12점 채우기 기사에 밀린다 — 2026-11-07 지적)
    # ── 우선 레인: 비트 내 상위권(z>=HOT_Z)은 쿼터를 무시하고 바로 게시 ──
    #    (중요 뉴스가 쿼터에 걸려 만료되는 것을 막는다)
    rdef = dict(_b.region_deficits(con))
    pool = []
    for b in _b.BEATS:
        cand = [r for r in con.execute(sql, (b, "", cat_hint.get(b, "-"))).fetchall()
                if r[4] not in bad]
        cand.sort(key=lambda r: -((r[7] + fb_adj(r[4])) *
                                  (0.55 + 0.45 * health.get(r[4], 0.8))))
        for r in cand[:15]:
            reg = (r[9] if len(r) > 9 and r[9] else _b.kw_region(r[1]))   # LLM 지역 라벨 우선
            pool.append((_b.z_score(r[7] + fb_adj(r[4]), b, stats), b, r, reg))
    pool.sort(key=lambda x: -x[0])

    recent_c = _b.beat_counts(con, window=5)
    for z, b, r, reg in pool:
        if z < _b.HOT_Z:
            break
        if recent_c.get(b, 0) >= 3:          # 같은 비트가 최근 5건 중 3건 이상이면 우선 레인 금지
            continue
        real = _candidate_ok(con, r, df, recent, cutoff_s, cfg["min_score"])
        if real:
            con.execute("UPDATE items SET beat=? WHERE id=? AND COALESCE(beat,'')=''",
                        (b, r[0]))
            con.commit()
            log(f"pick(hot): {b} score={r[7]:.1f} z={z:.1f} {r[1][:40]}")
            row = list(r[:8])
            row[3] = real
            return tuple(row)

    # ── 균형 레인: 목표비에서 모자란 비트부터. 단 그 비트의 평균 이상(z>=FLOOR_Z)만 ──
    by_beat = {}
    for z, b, r, reg in pool:
        if z >= _b.FLOOR_Z:
            by_beat.setdefault(b, []).append((z, b, r, reg))
    # 비트별로 '지역이 모자란' 후보를 우선 고른다
    best = {}
    for b, rows in by_beat.items():
        rows.sort(key=lambda x: (-(rdef.get(x[3], 0) > 0), -x[0]))
        best[b] = rows[0]
    for b, _deficit in _b.beat_deficits(con):
        if b not in best:
            continue
        z, _b2, r, reg = best[b]
        real = _candidate_ok(con, r, df, recent, cutoff_s, cfg["min_score"])
        if real:
            con.execute("UPDATE items SET beat=? WHERE id=? AND COALESCE(beat,'')=''",
                        (b, r[0]))
            con.commit()
            log(f"pick: {b} score={r[7]:.1f} z={z:.1f}")
            row = list(r[:8])
            row[3] = real
            return tuple(row)
    con.commit()
    return None


# ── 전송 ──────────────────────────────────────────────────────────────
CAT_KO = {"conflict": "분쟁", "politics": "정치", "economy": "경제", "google": "속보"}
CAT_ICON = {"conflict": "🔴", "politics": "🌐", "economy": "📉", "google": "⚡"}


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def notify(text, chat):
    """긴 글을 텔레그램 한도(4096)에 맞춰 나눠 보낸다. HTML 모드."""
    text = (text or "").strip()
    if not text:
        return False, "empty"
    parts, cur = [], ""
    for block in text.split("\n"):
        while len(block) > 3800:            # 한 줄이 한도보다 길면 강제 분할
            parts.append(block[:3800])
            block = block[3800:]
        if len(cur) + len(block) + 1 > 3800:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n{block}" if cur else block
    if cur:
        parts.append(cur)
    for p in parts:
        ok, err = send(p, chat)
        if not ok:                      # HTML 태그가 깨졌으면 평문으로 재시도
            ok, err = send(p, chat, html=False)
        if not ok:
            return False, f"{err} (part {len(parts)})"
        time.sleep(1)
    return True, ""


def related(con, title, exclude_url, main_feed=None, limit=2):
    """같은 사건을 다룬 다른 매체 링크를 찾는다. 본문 출처와 같은 매체는 뺀다."""
    rows = con.execute("""SELECT title, feed, url FROM items
        WHERE url != ? AND url NOT LIKE '%news.google.com%'
        ORDER BY id DESC LIMIT 250""", (exclude_url,)).fetchall()
    out, seen_feed, seen_url = [], set([main_feed]) if main_feed else set(), set()
    for t, f, u in rows:
        if f in seen_feed or u in seen_url or u == exclude_url:
            continue
        if similar(title, t) >= 0.30:
            out.append((f, u))
            seen_feed.add(f)
            seen_url.add(u)
        if len(out) >= limit:
            break
    return out


def compose(row, con, cfg):
    """기사 1건 → 정리형 메시지 (해설 본문 + 접히는 출처 블록)."""
    _id, title, descr, url, feed, cat, ts, sc = row
    import writer

    body = ""
    try:
        page = writer.fetch_html(url)
        ps = writer.paragraphs(page)
        body = writer.korean_smooth(
            writer.build(ps, writer.og_desc(page), lambda t: translate(t, con)))
    except Exception:
        body = ""
    if len(body) < 60:
        body = translate(descr[:400], con)
    # 제목만 되풀이하거나 너무 짧으면 보도하지 않는다
    if len(body) < 110 or not re.search(r"[요다]\s*$|[.!?]", body):
        return None
    # 인용/지시어로 시작하면 앞 문맥이 잘린 것 → 보도하지 않는다
    if re.match(r"""^[\s"'“”『「]|^(그러나|하지만|이러한|이 때문에|또한|그 결과)""", body):
        return None
    if len(re.split(r"(?<=[.!?])\s+", body)) < 2:
        return None
    if similar(body[:120], title) > 0.7:
        return None

    srcs = [(feed, url)] + related(con, title, url, feed)
    links = "\n".join(f'· <a href="{esc(u)}">{esc(f)}</a>' for f, u in srcs)
    return (f"{esc(body[:900])}\n\n"
            f"<blockquote expandable><b>출처</b>\n{links}</blockquote>")


LAST_MSG_ID = 0          # 마지막으로 성공한 sendMessage 의 message_id (feedback 매칭용)


def send(text, chat=None, html=True):
    global LAST_MSG_ID
    cfg = conf()
    chat = chat or cfg["channel"]
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and os.path.exists(f"{NEWSDIR}/tg.conf"):
        for line in open(f"{NEWSDIR}/tg.conf", encoding="utf-8"):
            if line.startswith("token="):
                token = line.split("=", 1)[1].strip()
    if not token:
        return False, "no token"
    payload = {"chat_id": chat, "text": text,
               "disable_web_page_preview": "true"}
    if html:
        payload["parse_mode"] = "HTML"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=data, headers={"User-Agent": "Leyley/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
        result = body.get("result") or {}
        if isinstance(result, dict) and result.get("message_id"):
            LAST_MSG_ID = int(result["message_id"])
        return bool(body.get("ok")), body.get("description", "")
    except Exception as e:
        return False, str(e)[:120]


def tick(con, cfg, force=False):
    last = meta_get(con, "last_post")
    now = now_utc()
    if not force and last:
        last_dt = dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        if (now - last_dt).total_seconds() < cfg["post_every_s"]:
            return None
    # 시간당 상한
    hour_ago = (now - dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC")
    n_hour = con.execute("SELECT COUNT(*) FROM items WHERE posted=1 AND posted_at>? AND note IS NULL",
                         (hour_ago,)).fetchone()[0]
    if not force and n_hour >= cfg["max_posts_per_hour"]:
        return None
    row = None
    for _ in range(4):
        cand = pick(con, cfg)
        if not cand:
            con.commit()
            return None
        text = compose(cand, con, cfg)
        if text:
            row, text = cand, text
            break
        con.execute("UPDATE items SET posted=1, note='no_body' WHERE id=?", (cand[0],))
        con.commit()
    if not row:
        return None
    ok, err = send(text, cfg["channel"])
    if ok:
        con.execute("UPDATE items SET posted=1, posted_at=?, note=NULL WHERE id=?",
                    (now.strftime("%Y-%m-%d %H:%M UTC"), row[0]))
        meta_set(con, "last_post", now.strftime("%Y-%m-%d %H:%M:%S"))
        con.commit()
        log(f"posted: {row[1][:70]}")
        return row
    log(f"send failed: {err}")
    return None


# ── 주간 점검 ─────────────────────────────────────────────────────────
def audit(con):
    cfg = conf()
    now = now_utc()
    week_ago = (now - dt.timedelta(days=7)).strftime("%Y-%m-%d %H:%M UTC")
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    posted = con.execute("SELECT COUNT(*) FROM items WHERE posted=1 AND note IS NULL").fetchone()[0]
    last7 = con.execute("SELECT COUNT(*) FROM items WHERE first_seen>?", (week_ago,)).fetchone()[0]
    last7p = con.execute("SELECT COUNT(*) FROM items WHERE posted_at>? AND note IS NULL",
                         (week_ago,)).fetchone()[0]
    by_feed = con.execute("""SELECT feed, COUNT(*) c FROM items
        WHERE first_seen>? AND feed NOT LIKE 'GN %'
        GROUP BY feed ORDER BY c DESC LIMIT 10""", (week_ago,)).fetchall()
    gn_total = con.execute("""SELECT COUNT(*) FROM items WHERE first_seen>? AND feed LIKE 'GN %'""",
                           (week_ago,)).fetchone()[0]
    per_day = con.execute("""SELECT substr(posted_at,1,10) d, COUNT(*) FROM items
        WHERE posted_at IS NOT NULL AND note IS NULL AND posted_at>?
        GROUP BY d ORDER BY d DESC LIMIT 7""", (week_ago,)).fetchall()
    runs = con.execute("SELECT at, ok, items, errors FROM runs WHERE kind='collect' ORDER BY id DESC LIMIT 40").fetchall()
    # 소스 생존 확인 (지금 직접)
    sources = nf.load_sources(f"{NEWSDIR}/sources.txt")
    dead, stale = [], []
    flaky = []
    from concurrent.futures import ThreadPoolExecutor

    def probe(s):
        cat, name, url = s
        err = ""
        for attempt in range(3):
            try:
                items = nf.parse_feed(nf.fetch(url, timeout=20))
                if not items:
                    return ("stale", name, "0 items")
                ts = [i["ts"] for i in items if i["ts"]]
                if ts and (now - max(ts)) > dt.timedelta(days=3):
                    return ("stale", name, f"newest {max(ts).strftime('%m-%d')}")
                return ("ok", name, str(len(items)))
            except Exception as e:
                err = f"{type(e).__name__}: {str(e)[:40]}"
                # 일시적 DNS·타임아웃은 재시도한다. 2026-11-08 점검에서 실패 8건이
                # 전부 'Errno -3 Try again'(DNS 순간 오류)였고 3초 뒤 전부 정상이었다.
                if "Errno -" in str(e) or "timed out" in str(e):
                    time.sleep(2 + attempt * 2)
                    continue
                break                       # 403·404·XML 아님은 확정 실패
        if "Errno -" in err or "timed out" in err:
            return ("flaky", name, err)
        return ("dead", name, err)

    with ThreadPoolExecutor(max_workers=12) as ex:
        for st, name, info in ex.map(probe, sources):
            if st == "dead":
                dead.append(f"{name} — {info}")
            elif st == "flaky":
                flaky.append(f"{name} — {info}")
            elif st == "stale":
                stale.append(f"{name} — {info}")

    lines = [f"<b>🗂 주간 OSINT 점검 · {kst(now)} KST</b>", ""]
    lines.append(f"수집 {last7}건 / 보도 {last7p}건 (7일). 누적 {total}건·게시 {posted}건.")
    lines.append(f"최근 수집 성공률: {sum(1 for r in runs if r[1])}/{len(runs)}회")
    lines.append("")
    lines.append("<b>소스 상태</b>")
    lines.append(f"정상 {len(sources)-len(dead)-len(stale)-len(flaky)} / 정체 {len(stale)} / "
                 f"실패 {len(dead)} / 일시오류 {len(flaky)}")
    if dead:
        lines.append("<b>실패 (교체 대상)</b>")
        lines += [f"· {esc(d)}" for d in dead[:12]]
    if flaky:
        lines.append("<b>일시오류 (재시도 후에도 DNS·타임아웃 — 다음 회차 확인)</b>")
        lines += [f"· {esc(d)}" for d in flaky[:12]]
    if stale:
        lines.append("<b>정체(3일 이상 새 글 없음)</b>")
        lines += [f"· {esc(s)}" for s in stale[:12]]
    lines.append("")
    lines.append("<b>많이 잡힌 출처</b>")
    lines += [f"· {esc(f)} {c}건" for f, c in by_feed[:8]]
    if gn_total:
        lines.append(f"· (Google News 경유 {gn_total}건)")
    if per_day:
        lines.append("")
        lines.append("<b>일별 보도</b> " + " · ".join(f"{d[5:]} {c}" for d, c in per_day))
    # 사용자에게 요청할 것
    WANT = [("NEWSAPI_KEY", "NewsAPI.org 무료 키 — 뉴스 검색 폭 확대"),
            ("GNEWS_KEY", "GNews.io 무료 키 — 백업 검색"),
            ("ACLED_KEY", "ACLED 액세스 키 — 분쟁 사건·사상자 데이터"),
            ("FIRMS_MAP_KEY", "NASA FIRMS 키 — 열점/폭발 탐지")]
    missing = [f"· {n} — {d}" for n, d in WANT if not os.environ.get(n)]
    lines.append("")
    if missing:
        lines.append("<b>받으면 좋을 것 (선택)</b>")
        lines += missing
    else:
        lines.append("<b>추가 요청 없음</b> — 현재 소스로 충분히 돌고 있다.")
    return "\n".join(lines)


def rotate_log(max_bytes=200000, keep_bytes=100000):
    """daemon.log 자가 로테이션 (keepalive 가 안 돌아도 무한 성장 방지)."""
    try:
        if os.path.getsize(LOG) <= max_bytes:
            return
        with open(LOG, "rb") as f:
            f.seek(-keep_bytes, os.SEEK_END)
            tail = f.read()
        cut = tail.find(b"\n")
        with open(LOG, "wb") as f:
            f.write(tail[cut + 1:] if cut > 0 else tail)
    except OSError:
        pass


def prune(con, keep_days=7):
    """오래된 미게시 기사 정리. 게시 기록은 통계용으로 남긴다."""
    cut = (now_utc() - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M UTC")
    n = con.execute("DELETE FROM items WHERE first_seen < ? AND posted_at IS NULL", (cut,)).rowcount
    con.commit()
    return n


# ── 데몬 ──────────────────────────────────────────────────────────────
def daemon():
    cfg = conf()
    con = db()
    next_collect = 0.0
    next_prune = 0.0
    cycle_start = time.time()
    last_hb = 0.0
    log("daemon start")
    while True:
        try:
            now = time.time()
            rotate_log()
            # 직전 사이클이 오래 걸렸으면 수집을 한 번 건너뛴다 (앱이 죽을 만큼 무거워지지 않게)
            if now >= next_collect and (time.time() - cycle_start) < 240:
                added, errors = collect(con)
                next_collect = now + cfg["collect_every_s"]
                log(f"collect +{added} err={len(errors)}")
                try:                                  # 비트 분류 (시간당 1회, 배치 1콜)
                    import beats
                    beats.backfill_kw(con)
                    n_cls = beats.classify_pending(con)
                    log(f"classify {n_cls}")
                except Exception as e:
                    log(f"classify error: {e}")
            if now - last_hb >= 60:          # 하트비트는 1분에 한 번만 (WAL 쓰기 절감)
                meta_set(con, "heartbeat", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                con.commit()
                last_hb = now
            if now >= next_prune:
                n = prune(con)
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                next_prune = now + 86400
                log(f"prune -{n} old items")
            mode = cfg.get("mode", "auto")
            try:
                import llm as _llm
                if mode != "auto" and _llm.available():
                    mode = "llm"      # 키가 있으면 자동으로 LLM 경로
            except Exception:
                pass
            if mode == "llm":
                last = meta_get(con, "last_post")
                due = True
                if last:
                    last_dt = dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
                    due = (now_utc() - last_dt).total_seconds() >= cfg["post_every_s"]
                if due:
                    llm_publish(con, cfg)
            elif mode == "auto":
                tick(con, cfg)      # 기계 작성 보도
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")
        cycle_start = now
        spent = time.time() - now
        if spent > 45:
            log(f"slow cycle {spent:.0f}s")
        time.sleep(60)


QUEUE = f"{BASE}/queue"


def make_draft(con, cfg):
    """후보 1건을 골라 기사 본문까지 붙여 큐 파일로 저장. (모델 작성용)"""
    import writer
    os.makedirs(QUEUE, exist_ok=True)
    # 오래된 초안 정리 (3시간 지나면 버린다)
    for fn in os.listdir(QUEUE):
        fp = os.path.join(QUEUE, fn)
        if fn.endswith(".json") and time.time() - os.path.getmtime(fp) > 10800:
            try:
                _d = json.load(open(fp, encoding="utf-8"))
                con.execute("UPDATE items SET note=NULL WHERE id=? AND posted=0", (_d["id"],))
                con.commit()
            except Exception:
                pass
            os.remove(fp)
    for _ in range(5):
        row = pick(con, cfg)
        if not row:
            return None
        _id, title, descr, url, feed, cat, ts, sc = row
        try:
            page = writer.fetch_html(url)
            paras = writer.paragraphs(page)
        except Exception:
            page, paras = None, []
        # 본문 페이지의 발행일로 마지막 검증 — 피드가 재게시·갱신 날짜를 주면
        # 몇 주 전 글이 '방금'으로 들어온다(2026-11-09 '2주 전 기사' 경로).
        if page:
            pd = nf.page_date(page)
            if pd and (now_utc() - pd) > dt.timedelta(hours=48):
                con.execute("UPDATE items SET posted=1, note='stale_page' WHERE id=?", (_id,))
                con.commit()
                log(f"stale_page({(now_utc() - pd).days}일): {title[:45]}")
                continue
        text = " ".join(paras)[:4000]
        thin = False
        if len(text) < 300:
            # 본문 추출 실패(페이월·JS 페이지) → RSS 요약문으로 대체
            if len(descr or "") >= 150:
                text = descr[:1500]
                log(f"body fallback(descr): {title[:50]}")
            elif (len(descr or "") >= 70 and sc >= 14
                  and not LOW_VALUE.search(title) and not LOW_VALUE_ANY.search(title)
                  and not PRICE_LIST.search(title)):
                # 짧은 스니펫만 있는 기사(페이월·구글뉴스 요약) → 2문장 속보로만 쓴다.
                # 2026-11-08: no_body 226건 중 180건이 이 경우였다(페이월 매체).
                text = descr[:600]
                thin = True
                log(f"body thin(descr {len(descr)}자): {title[:50]}")
            else:
                con.execute("UPDATE items SET posted=1, note='no_body' WHERE id=?", (_id,))
                con.commit()
                log(f"no_body: {feed[:16]} {title[:50]}")
                continue
        srcs = [{"feed": feed, "url": url}] + [{"feed": f, "url": u} for f, u in related(con, title, url, feed)]
        draft = {"id": _id, "title": title, "url": url, "feed": feed, "cat": cat,
                 "ts": ts, "score": sc, "text": text, "thin": thin, "sources": srcs}
        with open(f"{QUEUE}/{_id}.json", "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=1)
        con.execute("UPDATE items SET note='queued' WHERE id=?", (_id,))
        con.commit()
        return draft
    return None


class Lock:
    """동시 발행 방지 (데몬 vs 스케줄 태스크)."""
    def __init__(self, path=f"{BASE}/.publish.lock"):
        self.path = path
        self.fh = None

    def __enter__(self):
        import fcntl
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise RuntimeError("busy")
        return self

    def __exit__(self, *a):
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
        except Exception:
            pass


def posts_last_hour(con):
    hour_ago = (now_utc() - dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M UTC")
    return con.execute("SELECT COUNT(*) FROM items WHERE posted=1 AND posted_at>? AND note IS NULL",
                       (hour_ago,)).fetchone()[0]


def cap_hit(con, cfg):
    """시간당 게시 상한. 2026-11-08 이전에는 tick()(기계 작성 경로)에만 있어서
    llm 경로 실측 최대치가 13건/시간이었다(상한 10). 두 경로 모두 여기서 막는다."""
    n = posts_last_hour(con)
    cap = cfg["max_posts_per_hour"]
    if n < cap:
        return False
    stamp = now_utc().strftime("%Y-%m-%d %H")
    if meta_get(con, "cap_logged") != stamp:
        meta_set(con, "cap_logged", stamp)
        con.commit()
        log(f"cap: {n}건/1h >= {cap} — 상한까지 게시, 이번 사이클 건너뜀")
    return True


def llm_publish(con, cfg):
    """무료 LLM API로 초안 1건을 요약·게시. 실패하면 초안을 남겨 스케줄 태스크가 처리."""
    import llm
    if not llm.available():
        return None
    if cap_hit(con, cfg):
        return None
    try:
        lock = Lock()
        lock.__enter__()
    except RuntimeError:
        log("publish: busy (다른 프로세스가 발행 중)")
        return None
    try:
        return _llm_publish_locked(con, cfg, llm)
    finally:
        lock.__exit__()


def _llm_publish_locked(con, cfg, llm):
    # 실패로 남은 초안이 있으면 그것부터 처리 (쿼터·중복 생성 방지)
    pend = pending_drafts()
    dpath = pend[0] if pend else None
    if dpath:
        d = json.load(open(dpath, encoding="utf-8"))
    else:
        d = make_draft(con, cfg)
        dpath = f"{QUEUE}/{d['id']}.json" if d else None
    if not d:
        log("publish: 후보 없음(모두 필터됨)")
        return None
    body = llm.summarize(d["title"], d["text"], thin=bool(d.get("thin")))
    if body and not llm.is_complete(body, 60 if d.get("thin") else 80):      # 중간에 끊긴 요약은 내보내지 않는다
        log(f"llm: 잘린 요약 폐기 ({len(body)}자) — 초안 유지")
        body = ""
    if not body:
        log("llm: 실패, 초안 유지")
        return None
    # 요약문 수준 중복 차단 — 매체·제목이 달라도 같은 사건이면 여기서 걸린다.
    prev = dup_body(con, body)
    if prev:
        con.execute("UPDATE items SET posted=1, note='dup_body' WHERE id=?", (d["id"],))
        con.commit()
        try:
            os.remove(dpath or f"{QUEUE}/{d['id']}.json")
        except OSError:
            pass
        log(f"dup_body: {d['title'][:40]} ≒ {prev[:40]}")
        return None
    srcs = d["sources"][:3]
    links = "\n".join(f'· <a href="{esc(x["url"])}">{esc(x["feed"])}</a>' for x in srcs)
    text = f"{esc(body)}\n\n<blockquote expandable><b>출처</b>\n{links}</blockquote>"
    ok, err = send(text, cfg["channel"])
    if ok:
        con.execute("UPDATE items SET posted=1, posted_at=?, note=NULL, summary=? WHERE id=?",
                    (now_utc().strftime("%Y-%m-%d %H:%M UTC"), body, d["id"]))
        con.commit()
        try:
            os.remove(dpath or f"{QUEUE}/{d['id']}.json")
        except OSError:
            pass
        log(f"posted(llm): {d['title'][:60]}")
        meta_set(con, "last_post", now_utc().strftime("%Y-%m-%d %H:%M:%S"))
        con.commit()
        return d
    log(f"llm send failed: {err}")
    return None


def pending_drafts():
    if not os.path.isdir(QUEUE):
        return []
    out = []
    for fn in sorted(os.listdir(QUEUE)):
        if fn.endswith(".json"):
            out.append(os.path.join(QUEUE, fn))
    return out


def send_draft(con, draft_path, msg_file):
    try:
        lock = Lock()
        lock.__enter__()
    except RuntimeError:
        return False, "busy"
    try:
        return _send_draft_locked(con, draft_path, msg_file)
    finally:
        lock.__exit__()


def _send_draft_locked(con, draft_path, msg_file):
    d = json.load(open(draft_path, encoding="utf-8"))
    body = open(msg_file, encoding="utf-8").read().strip()
    if len(body) < 60:
        return False, "too short"
    srcs = d.get("sources") or [{"feed": d["feed"], "url": d["url"]}]
    links = "\n".join(f'· <a href="{esc(x["url"])}">{esc(x["feed"])}</a>' for x in srcs[:3])
    text = f"{esc(body)}\n\n<blockquote expandable><b>출처</b>\n{links}</blockquote>"
    prev = dup_body(con, body)
    if prev:
        return False, f"dup_body ≒ {prev[:30]}"
    ok, err = send(text, conf()["channel"])
    if ok:
        con.execute("UPDATE items SET posted=1, posted_at=?, note=NULL, summary=? WHERE id=?",
                    (now_utc().strftime("%Y-%m-%d %H:%M UTC"), body, d["id"]))
        con.commit()
        os.remove(draft_path)
        log(f"posted(model): {d['title'][:60]}")
    return ok, err


def main():
    os.makedirs(BASE, exist_ok=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "tick", "daemon", "status", "audit", "demo",
                                    "draft", "queue", "send", "publish", "notify"])
    ap.add_argument("--id")
    ap.add_argument("--file")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--chat", default="")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    con = db()
    cfg = conf()
    if a.cmd == "collect":
        n, e = collect(con)
        print(f"collected {n} new, {len(e)} errors")
    elif a.cmd == "tick":
        r = tick(con, cfg, force=a.force)
        print("nothing" if not r else "sent")
    elif a.cmd == "daemon":
        daemon()
    elif a.cmd == "status":
        print(json.dumps({
            "items": con.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "posted": con.execute("SELECT COUNT(*) FROM items WHERE note IS NULL AND posted=1").fetchone()[0],
            "last_collect": meta_get(con, "last_collect"),
            "last_post": meta_get(con, "last_post"),
            "heartbeat": meta_get(con, "heartbeat"),
            "min_score": cfg["min_score"],
        }, ensure_ascii=False, indent=1))
    elif a.cmd == "publish":
        d = llm_publish(con, cfg)
        print(json.dumps({"posted": d["title"] if d else None}, ensure_ascii=False))
    elif a.cmd == "draft":
        d = make_draft(con, cfg)
        print(json.dumps(d, ensure_ascii=False, indent=1) if d else "EMPTY")
    elif a.cmd == "queue":
        for p in pending_drafts():
            d = json.load(open(p, encoding="utf-8"))
            print(f'{p}\t{d["id"]}\t{d["score"]}\t{d["feed"]}\t{d["title"][:70]}')
    elif a.cmd == "notify":
        text = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
        chat = a.chat or cfg.get("owner_chat")
        if not chat:
            print("owner_chat 없음 — 공개 채널로 새지 않도록 전송 취소")
            sys.exit(2)
        ok, err = notify(text, chat)
        print(chat, "sent" if ok else f"failed: {err}")
    elif a.cmd == "send":
        p = f"{QUEUE}/{a.id}.json"
        print(send_draft(con, p, a.file))
    elif a.cmd == "audit":
        t = audit(con)
        print(t)
        if a.send:
            if not cfg.get("owner_chat"):
                print("owner_chat 없음 — 공개 채널로 새지 않도록 전송 취소")
                sys.exit(2)
            ok, err = send(t, cfg["owner_chat"])
            print("sent" if ok else f"fail {err}")


if __name__ == "__main__":
    main()
