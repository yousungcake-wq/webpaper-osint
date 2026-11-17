#!/usr/bin/env python3
"""선택 경로 회귀 시뮬레이션 — 네트워크 없이 `pick` 계약을 실제로 돌려본다.

  python3 tools/sim_pick.py

왜 필요한가: `tests/` 는 게이트 규칙을 고정하지만 **선택 경로 전체**(후보 조회 →
게이트 → 안전망 3·4차)는 손대기 어려웠다. 2026-11-17 후보 레코드를 `Cand` 로 바꾸고
안전망을 `pick` 안으로 옮기면서, "SQL 컬럼 순서와 이름이 맞는가"를 눈이 아니라
실행으로 확인하려고 만들었다.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import osint        # noqa: E402
import run_once     # noqa: E402

failed = []


def fresh(rows):
    """(title, beat, score, form, util, region) 목록 → in-memory DB."""
    con = run_once.make_con()
    for i, (title, beat, score, form, util, region) in enumerate(rows, 1):
        con.execute("""INSERT INTO items(id,key,title,descr,url,feed,cat,ts,score,posted,
                       beat,region,form,util) VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                    (i, f"k{i}", title, "d", f"https://ex.com/{i}", "Feed", "economy",
                     "2026-11-17 00:00 UTC", score, beat, region, form, util))
    con.commit()
    return con


def check(name, cond, extra=""):
    if not cond:
        failed.append(f"{name} {extra}")
    print(("ok  " if cond else "FAIL") + " " + name)


def main():
    cfg = osint.conf()                     # 안전망 3·4차는 osint.pick 안에 있다 (2026-11-17)

    # 1) 정상 후보 → Cand 로 돌아오고 url 이 채워진다
    con = fresh([
        ("Sudan ceasefire talks collapse as RSF advances on El Fasher", "CONFLICT", 30.0, "NEWS", 9, "AFRICA"),
        ("EU opens antitrust case over AI chip bundling", "TECH", 26.0, "NEWS", 8, "EUROPE"),
    ])
    got = osint.pick(con, cfg)
    check("returns_cand", isinstance(got, osint.Cand), type(got))
    check("url_present", bool(got and got.url and got.url.startswith("https://")), got)
    check("fields", bool(got and got.title and got.score and got.id), got)

    # 2) 형식 MARKET util 2 → 차단되고 note 가 남는다
    con = fresh([("Gold, silver rates today: check daily prices", "ECONOMY", 30.0, "MARKET", 2, "ASIA")])
    check("market_blocked", osint.pick(con, cfg) is None)
    note = con.execute("SELECT note FROM items WHERE note IS NOT NULL").fetchone()
    check("market_note", note and note[0].startswith("form:"), note)

    # 3) 만료 후보 → expired
    con = fresh([("Old story about a summit", "POLITICS", 30.0, "NEWS", 8, "EUROPE")])
    con.execute("UPDATE items SET ts='2000-01-01 00:00 UTC'")
    con.commit()
    check("expired", osint.pick(con, cfg) is None and con.execute(
        "SELECT note FROM items WHERE id=1").fetchone()[0] == "expired")

    # 4) 아프리카 비분쟁 차단 (지역 라벨이 맞는 경우)
    con = fresh([("Nigeria governor flags off road project in Dutse", "POLITICS", 30.0, "NEWS", 8, "AFRICA")])
    check("africa_non_conflict", osint.pick(con, cfg) is None)
    check("africa_note", con.execute("SELECT note FROM items WHERE id=1").fetchone()[0]
          == "africa_non_conflict")

    # 5) 안전망(3차) — 균형 레인이 비어도 후보를 낸다
    con = fresh([(f"Minor provincial budget dispute number {i} escalates tension", "POLITICS",
                  12.0 + i, "NEWS", 6, "ASIA") for i in range(6)])
    got = osint.pick(con, cfg)
    check("safety_net", isinstance(got, osint.Cand), got)
    check("safety_url", bool(got and got.url), got)

    print(f"\n실패 {len(failed)}건" if failed else "\n전부 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
