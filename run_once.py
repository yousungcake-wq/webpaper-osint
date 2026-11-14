#!/usr/bin/env python3
"""run_once.py — GitHub Actions 한 회차 실행기.

흐름: 상태 복원 → 수집(소스 샤드) → 비트 분류 → 균형 선택 → 요약 → 텔레그램 게시 → 상태 저장.

설계 메모
- 상태는 SQLite 대신 `state/posted.jsonl` (한 줄 = 게시 1건) 에 담는다.
  액션 러너는 매번 새 컨테이너라 DB 파일을 커밋해야 하는데, 바이너리 DB를
  커밋하면 히스토리가 하루 수 GB로 불어난다. append-only JSONL 은 건당 250B다.
- 매 회차 in-memory SQLite 에 상태를 되살려 넣고, 기존 osint.py/beats.py 의
  선택·중복·쿼터 로직을 **그대로** 재사용한다. (로직 재작성 = 품질 회귀)
- 소스는 한 번에 다 긁지 않고 SHARDS 등분해 번갈아 긁는다.
  96개 × 288회/일 = 27,648 요청은 과하다. 3등분이면 피드당 15분 주기.
"""
import contextlib
import datetime as dt
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import beats  # noqa: E402
import filters  # noqa: E402
import osint  # noqa: E402

STATE_DIR = os.path.join(BASE, "state")
STATE = os.path.join(STATE_DIR, "posted.jsonl")
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "5"))
DRY = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
SHARDS = max(1, int(os.environ.get("SHARDS", "2")))

SCHEMA = [
    """CREATE TABLE items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE, title TEXT, descr TEXT, url TEXT,
        feed TEXT, cat TEXT, ts TEXT, first_seen TEXT,
        score REAL, posted INTEGER DEFAULT 0, posted_at TEXT, note TEXT,
        beat TEXT, beat_src TEXT, region TEXT, cu TEXT, summary TEXT,
        form TEXT, util INTEGER)""",
    "CREATE TABLE tr(key TEXT PRIMARY KEY, ko TEXT)",
    "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE gnurl(gn TEXT PRIMARY KEY, real TEXT, at TEXT)",
    """CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT,
        ok INTEGER, items INTEGER, errors TEXT)""",
]


def make_con():
    con = sqlite3.connect(":memory:")
    for sql in SCHEMA:
        con.execute(sql)
    return con


POSTS_DIR = os.path.join(STATE_DIR, "posts")


def _state_files():
    """상태 파일 목록. state/posts/<날짜>/<시각>-<해시>.jsonl + 옛 posted.jsonl.

    왜 파일을 쪼개는가: 두 회차가 겹치면 같은 파일 끝에 동시에 append 해
    rebase 가 **충돌**한다. 충돌한 push 는 조용히 실패하고 그 게시 기록이 사라져
    다음 회차가 같은 기사를 다시 올린다(2026-11-09 06:50 사고).
    건마다 파일이 다르면 충돌이 원천적으로 없다.
    """
    out = [STATE] if os.path.exists(STATE) else []
    if os.path.isdir(POSTS_DIR):
        for d in sorted(os.listdir(POSTS_DIR)):
            sub = os.path.join(POSTS_DIR, d)
            if not os.path.isdir(sub):
                continue
            for fn in sorted(os.listdir(sub)):
                if fn.endswith(".jsonl"):
                    out.append(os.path.join(sub, fn))
    return out


def _rows():
    out = []
    for path in _state_files():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    out.sort(key=lambda d: d.get("a") or "")
    return out


def load_state(con):
    """state/posted.jsonl → in-memory items(게시분). 오래된 줄은 잘라낸다."""
    rows = _rows()
    cutoff = (osint.now_utc() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
    keep = [d for d in rows if (d.get("a") or "") >= cutoff]
    # 오래된 날짜 폴더는 통째로 지운다 (파일 경로에 날짜가 있다)
    if os.path.isdir(POSTS_DIR):
        cday = cutoff[:10]
        for d in sorted(os.listdir(POSTS_DIR)):
            if d < cday and os.path.isdir(os.path.join(POSTS_DIR, d)):
                for fn in os.listdir(os.path.join(POSTS_DIR, d)):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(POSTS_DIR, d, fn))
                with contextlib.suppress(OSError):
                    os.rmdir(os.path.join(POSTS_DIR, d))
    for d in keep:
        con.execute(
            """INSERT OR IGNORE INTO items(key,title,descr,url,feed,cat,ts,first_seen,
                score,posted,posted_at,note,beat,beat_src,region,cu,summary,form,util)
               VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)""",
            (d.get("k", ""), d.get("t", ""), "", d.get("u", ""), d.get("f", ""), d.get("c", ""),
             "", d.get("a", ""), d.get("s", 0), d.get("a", ""), d.get("n"),
             beats.norm_beat(d.get("b", "")), "state", d.get("r", ""),
             d.get("cu") or filters.canon_url(d.get("u", "")), d.get("x", ""),
             d.get("fm") or None, d.get("ut")))
    con.commit()
    return len(keep)


def append_state(d, con, row_id):
    """게시 1건을 **제 파일**에 기록한다 (충돌 없는 append).

    state/posts/<날짜>/<시각>-<키>.jsonl — 회차가 겹쳐도 같은 파일을 건드리지 않는다.
    요약문(x)과 정규화 주소(cu)도 같이 남긴다. 다음 회차의 중복 판정 재료다.
    """
    r = con.execute("SELECT beat, region, key, cat, summary, cu, form, util FROM items"
                    " WHERE id=?", (row_id,)).fetchone() or ("", "", "", "", "", "", None, None)
    beat = r[0] or beats.kw_beat(d.get("title", ""), d.get("cat", ""))
    region = r[1] or beats.kw_region(d.get("title", ""))
    now = osint.now_utc()
    url = d.get("url") or ""
    rec = {"k": r[2] or "", "u": url[:300], "cu": r[5] or filters.canon_url(url),
           "t": (d.get("title") or "")[:180],
           "f": (d.get("feed") or "")[:40], "c": r[3] or d.get("cat", ""),
           "b": beat, "r": region, "s": d.get("score", 0),
           "x": (r[4] or "")[:1200],
           "fm": r[6] or "", "ut": r[7],
           "h": (d.get("headline") or "")[:80],          # 사이트용 한국어 제목
           "m": getattr(osint, "LAST_MSG_ID", 0),      # 반응(👎/👍) 매칭용
           "a": now.strftime("%Y-%m-%d %H:%M UTC")}
    sub = os.path.join(POSTS_DIR, now.strftime("%Y-%m-%d"))
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, f"{now.strftime('%H%M%S')}-{(r[2] or 'x')[:8]}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def shard_sources():
    """이번 회차에 긁을 소스만 남기도록 news_fetch.load_sources 를 감싼다."""
    original = osint.nf.load_sources
    idx = int(time.time() // 300) % SHARDS        # 5분 슬롯마다 다음 샤드

    def load(path):
        srcs = original(path)
        if SHARDS <= 1:
            return srcs
        return [s for i, s in enumerate(srcs) if i % SHARDS == idx]

    osint.nf.load_sources = load
    return idx


FORMS_DIR = os.path.join(STATE_DIR, "forms")        # 기사 key → 비트·지역·형식·유용도
FORMS_KEEP_H = 48

# 왜 판정을 저장하나: 회차는 매번 새 프로세스라 items 테이블이 다시 만들어진다.
# 그래서 5분마다 같은 기사를 60건씩 **처음부터 다시 분류**했고, 그 콜이 Groq 무료
# 하루 한도(200k 토큰)를 3~4배 초과해 하루 종일 429로 떨어졌다
# (2026-11-12 실측: used 198,246 / limit 200,000). 한 기사는 한 번만 분류한다.
#
# 왜 파일 하나가 아니라 회차마다 새 파일인가: 하나의 JSONL 을 계속 고쳐 쓰면
# 커밋마다 파일 전체가 새 blob 이 되어 히스토리가 하루 수십 MB 로 분다
# (state/posts 를 건당 파일로 쪼갠 것과 같은 이유).


def _form_files():
    if not os.path.isdir(FORMS_DIR):
        return []
    out = []
    for d in sorted(os.listdir(FORMS_DIR)):
        sub = os.path.join(FORMS_DIR, d)
        if not os.path.isdir(sub):
            continue
        out += [os.path.join(sub, fn) for fn in sorted(os.listdir(sub))
                if fn.endswith(".jsonl")]
    return out


BULK_EVERY_MIN = int(os.environ.get("BULK_EVERY_MIN", "60") or 60)
BULK_MARK = os.path.join(FORMS_DIR, "last_bulk.txt")


def _bulk_due():
    """비트·지역 배치를 지금 돌릴 때인가. 실패한 회차는 다음 회차에 다시 시도한다."""
    try:
        with open(BULK_MARK, encoding="utf-8") as f:
            last = dt.datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M UTC")
        last = last.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return True
    age = (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 60
    return age >= BULK_EVERY_MIN


def _mark_bulk():
    os.makedirs(FORMS_DIR, exist_ok=True)
    with open(BULK_MARK, "w", encoding="utf-8") as f:
        f.write(dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))


def load_forms():
    """지난 회차 분류 판정을 읽는다(key → 레코드)."""
    out = {}
    for path in _form_files():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("k"):
                    out[d["k"]] = d
    return out


def _prune_forms(now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(hours=FORMS_KEEP_H)).strftime("%Y-%m-%d")
    if not os.path.isdir(FORMS_DIR):
        return
    for d in sorted(os.listdir(FORMS_DIR)):
        if d >= cutoff:
            continue
        sub = os.path.join(FORMS_DIR, d)
        if not os.path.isdir(sub):
            continue
        for fn in os.listdir(sub):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(sub, fn))
        with contextlib.suppress(OSError):
            os.rmdir(sub)


def apply_forms(con):
    """캐시된 판정을 이번 회차 items 에 앉힌다."""
    forms = load_forms()
    _prune_forms()
    n = 0
    for k, d in forms.items():
        cur = con.execute("SELECT 1 FROM items WHERE key=?", (k,)).fetchone()
        if not cur:
            continue
        con.execute("""UPDATE items SET beat=?, beat_src='llm', region=?, form=?, util=?
                       WHERE key=?""",
                    (d.get("b") or "", d.get("r") or "", d.get("fm") or None,
                     d.get("ut"), k))
        n += 1
    con.commit()
    return n


def save_forms(con):
    """이번 회차에 새로 분류한 것만 **새 파일**로 남긴다(덮어쓰지 않는다)."""
    forms = load_forms()
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for k, b, r, fm, ut in con.execute(
            """SELECT key, beat, region, form, util FROM items
               WHERE beat_src='llm' AND key IS NOT NULL AND key != ''"""):
        if k in forms:
            continue
        rows.append({"k": k, "b": b or "", "r": r or "", "fm": fm or "", "ut": ut})
    if not rows or DRY:
        return 0
    sub = os.path.join(FORMS_DIR, now.strftime("%Y-%m-%d"))
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, f"{now.strftime('%H%M%S')}-{len(rows)}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"forms: +{len(rows)} → {os.path.relpath(path, STATE_DIR)}")
    return len(rows)


def install_loose_pick():
    """균형 레인이 비었을 때를 위한 3·4차 안전망.

    균형 배분은 '목표비에서 모자란 비트'가 있어야 후보를 낸다. 5분 주기로 돌면
    목표비가 이미 채워져 있어 아무것도 못 고르는 회차가 잦다(그때 침묵하면 채널이 빈다).
    그래서 두 단계를 더 둔다.
      3차 — 최근 게시가 가장 적은 비트부터, 그 비트의 상위 후보
      4차 — 그래도 없으면 점수 상위에서 품질 하한(최소점수+3)을 넘는 첫 건
    평상시(1·2차) 동작은 건드리지 않는다.
    """
    original = osint.pick

    def pick(con, cfg):
        row = original(con, cfg)
        if row:
            return row

        cutoff = osint.now_utc() - dt.timedelta(hours=cfg["max_age_hours"])
        cutoff_s = cutoff.strftime("%Y-%m-%d %H:%M UTC")
        _cut = (osint.now_utc() - dt.timedelta(hours=36)).strftime("%Y-%m-%d %H:%M UTC")
        recent = [r[0] for r in con.execute(
            "SELECT title FROM items WHERE posted=1 AND posted_at>=? "
            "ORDER BY posted_at DESC LIMIT 400", (_cut,))]
        df = osint.df_map(con)

        def usable(r):
            title = r[1] or ""
            if (r[7] or 0) < cfg["min_score"] + 3:
                return False
            if (osint.PRICE_LIST.search(title) or osint.LOW_VALUE.search(title)
                    or osint.LOW_VALUE_ANY.search(title) or osint.COURTESY.search(title)
                    or osint.NOISE_TITLE.search(title)):
                return False
            # 2026-11-12 '빈 슬롯 허용': 안전망은 **품질이 확실할 때만** 채운다.
            # 형식이 뉴스가 아니면 유용도 5 이상(본선 기준 4보다 높다)이어야 하고,
            # 유용도를 모르면 그냥 통과시킨다(정규식 백스톱이 잡는다).
            util = r[11] if len(r) > 11 else None
            form = (r[10] if len(r) > 10 else None) or "NEWS"
            if util is not None and form != "NEWS" and util < 5:
                return False
            return True

        def take(r):
            real = osint._candidate_ok(con, r, df, recent, cutoff_s, cfg["min_score"])
            if not real:
                return None
            con.commit()
            return (r[0], r[1], r[2], real) + tuple(r[4:8])


        # 3차: 최근 게시가 적은 비트부터
        heat = osint.story_heat(con)
        counts = beats.beat_counts(con, window=12)
        for b in sorted(beats.BEATS, key=lambda x: counts.get(x, 0)):
            cands = con.execute("""SELECT id,title,descr,url,feed,cat,ts,score,beat,region,form,util
                    FROM items WHERE posted=0 AND beat=? ORDER BY score DESC LIMIT 8""",
                                (b,)).fetchall()
            # 같은 이야기가 하루 상한을 넘었으면 뒤로 미룬다(사건 예산, 2026-11-12)
            cands.sort(key=lambda r: -((r[7] or 0) - osint.story_penalty(heat, r[1])))
            for r in cands:
                if not usable(r):
                    continue
                got = take(r)
                if got:
                    print(f"pick(3차·{b} 최근{counts.get(b, 0)}건): {r[7]:.1f} {r[1][:50]}")
                    return got

        # 4차: 점수 상위
        rows = con.execute("""SELECT id,title,descr,url,feed,cat,ts,score,beat,region,form,util
                FROM items WHERE posted=0 ORDER BY score DESC LIMIT 40""").fetchall()
        rows.sort(key=lambda r: -((r[7] or 0) - osint.story_penalty(heat, r[1])))
        for r in rows:
            if not usable(r):
                continue
            got = take(r)
            if got:
                print(f"pick(4차·점수): {r[7]:.1f} form={r[10]} util={r[11]} {r[1][:50]}")
                return got
        con.commit()
        return None

    osint.pick = pick


def _git(args):
    return subprocess.run(args, cwd=BASE, capture_output=True, text=True, timeout=180)


def sync_state():
    """회차 시작 전에 원격 상태를 당겨온다.

    5분 전 회차가 방금 올린 글을 이 회차가 모르면 같은 기사를 또 올린다
    (2026-11-09 06:50 — 13초 간격 이중 게시). 그래서 선택 **전에** 최신 상태를 본다.
    """
    r = _git(["git", "pull", "--rebase", "--autostash", "-q", "origin", "main"])
    if r.returncode != 0:
        print("state sync:", (r.stderr or r.stdout or "")[:120])
    return r.returncode == 0


def commit_state():
    """게시 직후 상태를 저장소에 커밋한다. 실패해도 다음 회차를 막지 않는다."""
    if os.environ.get("COMMIT_STATE", "") not in ("1", "true", "yes"):
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    try:
        try:                                  # 사이트 피드(site/feed.json)도 같이 커밋한다
            import feedgen
            data, written = feedgen.write()
            print(f"feed: {data['count']}건 · {data['pages']}쪽 (전체 {data['total']}건)")
        except Exception as e:                # 피드 생성 실패가 게시를 막지는 않는다
            print("feedgen 오류:", e)
        _git(["git", "add", "state", "site"])
        c = _git(["git", "commit", "-m", f"state: {stamp} UTC"])
        if c.returncode != 0:
            print("state: 커밋할 변경 없음")
            return
        for attempt in range(4):            # 겹친 회차와의 push 경쟁은 재시도로 푼다
            _git(["git", "pull", "--rebase", "--autostash", "-q"])
            p = _git(["git", "push", "-q"])
            if p.returncode == 0:
                print("state push: ok")
                return
            time.sleep(2 + 3 * attempt)
            print(f"state push 재시도 {attempt + 1}: {(p.stderr or '')[:90]}")
        # 여기까지 왔으면 이 기록은 원격에 없다 → 다음 회차 pull 때 합쳐진다.
        print("state push FAILED — 원격 미반영")
    except Exception as e:                      # 커밋 실패가 게시를 되돌리지는 않는다
        print("git error:", e)


def cycle():
    """한 회차: 복원 → 수집 → 분류 → 선택 → 요약 → 게시 → 상태 저장."""
    t0 = time.time()
    cfg = osint.conf()
    sync_state()                     # 선택 전에 원격 최신 상태를 본다
    con = make_con()
    n_state = load_state(con)

    idx = shard_sources()
    added, errors = osint.collect(con, hours=8)
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M}] collect +{added} err={len(errors)} "
          f"(샤드 {idx}/{SHARDS})")
    for e in errors[:3]:
        print("  err:", e)

    # 키워드 1차 분류는 매 회차 무료로 돌린다. 예전에는 LLM 배치가 이 역할까지 했는데
    # (회차당 60건), 배치를 시간당 1회로 줄이면 그 사이 후보가 비트 없이 남아
    # pick 이 아무것도 못 고른다 — 채널이 최대 1시간 조용해진다(2026-11-12 실측).
    beats.backfill_kw(con)
    apply_forms(con)                 # 지난 회차에 분류한 판정을 되살린다
    n_beat = 0
    if _bulk_due():                  # 비트·지역 배치는 시간당 1회 (토큰 한도)
        n_beat, n_llm = beats.classify_pending(con)
        if n_llm:
            _mark_bulk()
    save_forms(con)
    got = osint.llm_publish(con, cfg)
    if not DRY:
        save_forms(con)              # pick 안에서 단건 분류한 것까지 저장
    if got and not DRY:
        append_state(got, con, got.get("id"))
        commit_state()
        print(f"posted: {got.get('title', '')[:70]}")
    elif got:
        print(f"[DRY RUN] 게시 예정: {got.get('title', '')[:70]}")
    else:
        print("이번 회차 게시 없음")
    # 이번 회차에 무엇이 걸렸는지 한 줄로 남긴다(Actions 로그에서 게이트가 도는지 보이게).
    gs = con.execute("""SELECT note, COUNT(*) FROM items
        WHERE note LIKE 'form%' OR note IN ('dup','digest','opinion','sport','life','promo',
              'stockpick','africa_non_conflict','africa_feed_non_conflict','expired','noise')
        GROUP BY 1 ORDER BY 2 DESC LIMIT 8""").fetchall()
    print("  gate: " + (" ".join(f"{n}={c}" for n, c in gs) if gs else "없음"))
    print(f"  이력 {n_state}건 · 분류 {n_beat} · {time.time() - t0:.0f}s")
    return got



# ── 조용한 정지 감시 (2026-11-10) ─────────────────────────────────────────────
# 워크플로가 success 로 끝나도 "게시 없음"이면 채널은 조용하다. 실제로 11-08 에
# 3시간 공백이 있었고 아무도 몰랐다. LLM 두 곳이 동시에 429/503 이면 요약이 안 나와
# 게시가 멈추는데, 로그에만 남고 알림은 없다.
# → 마지막 게시가 ALERT_GAP_MIN 분보다 오래되면 **워크플로를 실패시킨다**(GitHub 알림).
#   3시간에 한 번만 운다(마커 파일). 블록 모드(LOOP>0)는 스스로 도는 중이라 건너뛴다.
ALERT_GAP_MIN = int(os.environ.get("ALERT_GAP_MIN", "90") or 90)
ALERT_EVERY_H = 3


def _last_post_utc():
    """state/posts/<날짜>/<HHMMSS>-<해시>.jsonl 파일명에서 마지막 게시 시각을 얻는다."""
    best = None
    for d in glob.glob(os.path.join(STATE_DIR, "posts", "*")):
        day = os.path.basename(d)
        for f in glob.glob(os.path.join(d, "*.jsonl")):
            hhmmss = os.path.basename(f).split("-")[0]
            if len(hhmmss) != 6 or not hhmmss.isdigit():
                continue
            try:
                t = dt.datetime.strptime(day + hhmmss, "%Y-%m-%d%H%M%S")
            except ValueError:
                continue
            if best is None or t > best:
                best = t
    return best


def watchdog():
    last = _last_post_utc()
    if last is None:
        return
    age = (dt.datetime.utcnow() - last).total_seconds() / 60
    if age < ALERT_GAP_MIN:
        return
    marker = os.path.join(STATE_DIR, "last_alert.txt")
    try:
        prev = dt.datetime.fromisoformat(open(marker).read().strip())
    except Exception:
        prev = None
    now = dt.datetime.utcnow()
    if prev and (now - prev).total_seconds() < ALERT_EVERY_H * 3600:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    open(marker, "w").write(now.isoformat(timespec="seconds"))
    commit_state()        # 마커를 저장소에 남겨야 다음 러너가 3시간 스로틀을 지킨다
    print(f"::error::게시 정지 {age:.0f}분 — 마지막 게시 {last} UTC", flush=True)
    sys.exit(1)

def main():
    install_loose_pick()
    if DRY:
        def fake_send(text, chat=None, html=True):
            print("[DRY RUN] 전송 생략, 본문 미리보기 ↓")
            print(text)
            return True, None
        osint.send = fake_send

    # LOOP_MINUTES>0 이면 그 시간 동안 5분 간격으로 계속 돈다.
    # GitHub 스케줄이 이 계정에서 동작하지 않아, 한 번 트리거되면 몇 시간을
    # 스스로 돌게 만들었다(트리거는 폰 스케줄 태스크가 몇 시간에 한 번 nudges).
    # 입력값은 워크플로 입력에서 온다(외부에서도 지정 가능). 숫자가 아니거나
    # 과도하게 길면 기본값으로 되돌린다 — 잘못된 값으로 잡을 오래 물고 있지 않게.
    try:
        loop_min = int(str(os.environ.get("LOOP_MINUTES", "0")).strip() or 0)
    except ValueError:
        loop_min = 0
    try:
        max_cycles = int(str(os.environ.get("MAX_CYCLES", "0")).strip() or 0)
    except ValueError:
        max_cycles = 0
    loop_min = max(0, min(loop_min, 340))
    deadline = time.time() + loop_min * 60
    n = 0
    while True:
        try:
            cycle()
        except Exception:
            traceback.print_exc()          # 한 회차 실패로 루프 전체를 죽이지 않는다
        n += 1
        if loop_min <= 0:
            watchdog()                     # 단발 회차에서만. 블록은 스스로 도는 중이다
        if max_cycles and n >= max_cycles:
            break
        if loop_min <= 0:
            break
        wait = (int(time.time() // 300) + 1) * 300 - time.time()   # 다음 5분 경계
        if time.time() + wait > deadline:
            break
        time.sleep(max(1.0, wait))
    print(f"done: {n}회차")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    # 수집 스레드가 DNS(getaddrinfo)에서 멈추면 ThreadPoolExecutor 의 atexit 조인이
    # 영원히 기다려 잡이 끝나지 않는다(2026-11-08: 게시는 끝났는데 run 이 15분 타임아웃까지
    # in_progress 로 남았다). 상태 커밋은 이미 끝났으므로 프로세스를 확실히 종료한다.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
