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
    """비트 균형 레인이 비었을 때를 위한 안전망.

    균형 배분은 '목표비에서 모자란 비트'가 있어야 후보를 낸다. 수집을 샤드로 나누면
    회차당 후보 풀이 작아져서, 조건에 맞는 비트가 없으면 아무것도 못 고르고 침묵한다
    (2026-11-07 폰에서 45분 공백 사고가 같은 원인이었다). 그럴 때만 점수 상위에서
    필터를 통과하는 첫 건을 쓴다. 평상시 동작은 건드리지 않는다.
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
        for r in con.execute("""SELECT id,title,descr,url,feed,cat,ts,score FROM items
                WHERE posted=0 ORDER BY score DESC LIMIT 40""").fetchall():
            real = osint._candidate_ok(con, r, df, recent, cutoff_s, cfg["min_score"])
            if real:
                con.commit()
                print(f"loose pick: {r[7]:.1f} {r[1][:60]}")
                return (r[0], r[1], r[2], real) + tuple(r[4:8])
        con.commit()
        return None

    osint.pick = pick


def main():
    t0 = time.time()
    cfg = osint.conf()
    install_loose_pick()
    con = make_con()
    n_state = load_state(con)
    print(f"state: 게시 이력 {n_state}건 (최근 {KEEP_DAYS}일)")

    if DRY:
        def fake_send(text, chat=None, html=True):
            print("[DRY RUN] 전송 생략, 본문 미리보기 ↓")
            print(text)
            return True, None
        osint.send = fake_send

    idx = shard_sources()
    added, errors = osint.collect(con, hours=8)
    print(f"collect: +{added}건, 오류 {len(errors)} (샤드 {idx}/{SHARDS})")
    for e in errors[:5]:
        print("  err:", e)

    n_beat = beats.classify_pending(con)
    print(f"classify: {n_beat}건")

    got = osint.llm_publish(con, cfg)
    if got and not DRY:
        rid = got.get("id")
        append_state(got, con, rid)
        print(f"posted: {got.get('title', '')[:70]}")
    elif got:
        print(f"[DRY RUN] 게시 예정: {got.get('title', '')[:70]}")
    else:
        print("이번 회차 게시 없음")
    print(f"elapsed: {time.time() - t0:.1f}s")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### OSINT run\n\n- 수집 +{added} (오류 {len(errors)}, 샤드 {idx}/{SHARDS})\n"
                    f"- 분류 {n_beat}\n"
                    f"- 게시: {('**' + got.get('title', '')[:90] + '**') if got else '없음'}\n"
                    f"- 소요 {time.time() - t0:.0f}s\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
