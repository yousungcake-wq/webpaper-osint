#!/usr/bin/env python3
"""게시 기록(state/posts/*.jsonl) 계약 테스트 — 네트워크 불필요.

왜 있나: 2026-11-17 에 이 경로가 **한 번에 무너졌다**. `append_state` 안에서
`fetchone()` 이 돌려주는 맨 튜플을 이름 있는 레코드처럼 다루다 AttributeError 가
났고, 그 예외는 `cycle()` 밖으로 나가 `main()` 의 try 에 먹혔다. 결과는:
  · 텔레그램에는 계속 게시된다 (전송이 기록보다 먼저다)
  · state 커밋이 없으니 사이트 피드가 **멈춘다**
  · 런은 success 로 보인다 (예외를 잡아 6시간짜리 루프를 죽이지 않으려는 설계)
DRY 실행으로는 절대 잡히지 않는다 — DRY 는 `append_state` 를 건너뛴다.
그래서 이 테스트는 기록 경로 자체를 직접 부른다.

  python3 tests/test_state_record.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_once     # noqa: E402

failed = []


def check(name, cond, extra=""):
    if cond:
        print("ok", name)
    else:
        failed.append(name)
        print("FAIL", name, extra)


con = run_once.make_con()
con.execute("""INSERT INTO items(id,key,title,descr,url,feed,cat,ts,score,posted,beat,region,
               form,util,summary,cu) VALUES(1,'k1','Title here','d','https://ex.com/1',
               'Feed X','tech','2026-11-17 00:00 UTC',20.0,1,'TECH','US','NEWS',8,
               '요약문이에요.','https://ex.com/1')""")
con.commit()

tmp = tempfile.mkdtemp()
run_once.POSTS_DIR = tmp          # 저장소 대신 임시 폴더에 쓴다
draft = {"title": "Title here", "url": "https://ex.com/1", "feed": "Feed X",
         "cat": "tech", "score": 20.0, "headline": "한국어 제목"}

path = run_once.append_state(draft, con, 1)
check("file_written", os.path.exists(path), path)
rec = json.loads(open(path, encoding="utf-8").read().strip())
check("beat", rec.get("b") == "TECH", rec.get("b"))
check("region", rec.get("r") == "US", rec.get("r"))
check("summary", rec.get("x") == "요약문이에요.", rec.get("x"))
check("form_util", rec.get("fm") == "NEWS" and rec.get("ut") == 8, (rec.get("fm"), rec.get("ut")))
check("headline", rec.get("h") == "한국어 제목", rec.get("h"))
check("key", bool(rec.get("k")), rec.get("k"))

# 행을 못 찾아도 (동시성·정리로 사라진 경우) 기본값으로 기록한다 — 예외로 회차를 죽이지 않는다.
path2 = run_once.append_state({"title": "Ghost", "url": "https://ex.com/2"}, con, 999)
rec2 = json.loads(open(path2, encoding="utf-8").read().strip())
check("missing_row_ok", rec2.get("t") == "Ghost", rec2.get("t"))
check("missing_row_beat_from_title", isinstance(rec2.get("b"), str), rec2.get("b"))

print()
print("전부 통과" if not failed else f"실패 {len(failed)}건: {failed}")
sys.exit(1 if failed else 0)
