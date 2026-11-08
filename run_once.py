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
import datetime as dt
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
        beat TEXT, beat_src TEXT, region TEXT)""",
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


def _rows():
    if not os.path.exists(STATE):
        return []
    out = []
    with open(STATE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def load_state(con):
    """state/posted.jsonl → in-memory items(게시분). 오래된 줄은 잘라낸다."""
    rows = _rows()
    cutoff = (osint.now_utc() - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M UTC")
    keep = [d for d in rows if (d.get("a") or "") >= cutoff]
    if len(keep) != len(rows):                      # 잘라냈으면 파일도 줄인다
        with open(STATE, "w", encoding="utf-8") as f:
            for d in keep:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    for d in keep:
        con.execute(
            """INSERT OR IGNORE INTO items(key,title,descr,url,feed,cat,ts,first_seen,
                score,posted,posted_at,note,beat,beat_src,region)
               VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
            (d.get("k", ""), d.get("t", ""), "", "", d.get("f", ""), d.get("c", ""),
             "", d.get("a", ""), d.get("s", 0), d.get("a", ""), d.get("n"),
             d.get("b", ""), "state", d.get("r", "")))
    con.commit()
    return len(keep)


def append_state(d, con, row_id):
    """게시 1건을 상태 파일 끝에 붙인다."""
    r = con.execute("SELECT beat, region, key, cat FROM items WHERE id=?", (row_id,)).fetchone() or ("", "", "", "")
    beat = r[0] or beats.kw_beat(d.get("title", ""), d.get("cat", ""))
    region = r[1] or beats.kw_region(d.get("title", ""))
    rec = {"k": r[2] or "", "t": (d.get("title") or "")[:180],
           "f": (d.get("feed") or "")[:40], "c": r[3] or d.get("cat", ""),
           "b": beat, "r": region, "s": d.get("score", 0),
           "a": osint.now_utc().strftime("%Y-%m-%d %H:%M UTC")}
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


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
        recent = [r[0] for r in con.execute(
            "SELECT title FROM items WHERE posted=1 ORDER BY posted_at DESC LIMIT 80")]
        df = osint.df_map(con)

        def usable(r):
            title = r[1] or ""
            if (r[7] or 0) < cfg["min_score"] + 3:
                return False
            if (osint.PRICE_LIST.search(title) or osint.LOW_VALUE.search(title)
                    or osint.LOW_VALUE_ANY.search(title) or osint.COURTESY.search(title)
                    or osint.NOISE_TITLE.search(title)):
                return False
            return True

        def take(r):
            real = osint._candidate_ok(con, r, df, recent, cutoff_s, cfg["min_score"])
            if not real:
                return None
            con.commit()
            return (r[0], r[1], r[2], real) + tuple(r[4:8])

        # 3차: 최근 게시가 적은 비트부터
        counts = beats.beat_counts(con, window=12)
        for b in sorted(beats.BEATS, key=lambda x: counts.get(x, 0)):
            cands = con.execute("""SELECT id,title,descr,url,feed,cat,ts,score FROM items
                    WHERE posted=0 AND beat=? ORDER BY score DESC LIMIT 8""", (b,)).fetchall()
            for r in cands:
                if not usable(r):
                    continue
                got = take(r)
                if got:
                    print(f"pick(3차·{b} 최근{counts.get(b, 0)}건): {r[7]:.1f} {r[1][:50]}")
                    return got

        # 4차: 점수 상위
        for r in con.execute("""SELECT id,title,descr,url,feed,cat,ts,score FROM items
                WHERE posted=0 ORDER BY score DESC LIMIT 40""").fetchall():
            if not usable(r):
                continue
            got = take(r)
            if got:
                print(f"pick(4차·점수): {r[7]:.1f} {r[1][:50]}")
                return got
        con.commit()
        return None

    osint.pick = pick


def commit_state():
    """게시 직후 상태를 저장소에 커밋한다. 실패해도 다음 회차를 막지 않는다."""
    if os.environ.get("COMMIT_STATE", "") not in ("1", "true", "yes"):
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")

    def run(args):
        return subprocess.run(args, cwd=BASE, capture_output=True, text=True, timeout=180)

    try:
        run(["git", "add", "state/posted.jsonl"])
        c = run(["git", "commit", "-m", f"state: {stamp} UTC"])
        if c.returncode != 0:
            return
        run(["git", "pull", "--rebase", "--autostash", "-q"])
        p = run(["git", "push", "-q"])
        print("state push:", "ok" if p.returncode == 0 else (p.stderr or "")[:100])
    except Exception as e:                      # 커밋 실패가 게시를 되돌리지는 않는다
        print("git error:", e)


def cycle():
    """한 회차: 복원 → 수집 → 분류 → 선택 → 요약 → 게시 → 상태 저장."""
    t0 = time.time()
    cfg = osint.conf()
    con = make_con()
    n_state = load_state(con)

    idx = shard_sources()
    added, errors = osint.collect(con, hours=8)
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M}] collect +{added} err={len(errors)} "
          f"(샤드 {idx}/{SHARDS})")
    for e in errors[:3]:
        print("  err:", e)

    n_beat = beats.classify_pending(con)
    got = osint.llm_publish(con, cfg)
    if got and not DRY:
        append_state(got, con, got.get("id"))
        commit_state()
        print(f"posted: {got.get('title', '')[:70]}")
    elif got:
        print(f"[DRY RUN] 게시 예정: {got.get('title', '')[:70]}")
    else:
        print("이번 회차 게시 없음")
    print(f"  이력 {n_state}건 · 분류 {n_beat} · {time.time() - t0:.0f}s")
    return got


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
    loop_min = int(os.environ.get("LOOP_MINUTES", "0"))
    max_cycles = int(os.environ.get("MAX_CYCLES", "0"))
    deadline = time.time() + loop_min * 60
    n = 0
    while True:
        try:
            cycle()
        except Exception:
            traceback.print_exc()          # 한 회차 실패로 루프 전체를 죽이지 않는다
        n += 1
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
