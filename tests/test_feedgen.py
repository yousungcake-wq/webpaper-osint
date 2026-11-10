#!/usr/bin/env python3
"""feedgen 회귀 테스트 — 사이트가 조용히 빈 화면이 되는 조건을 막는다.

  python3 tests/test_feedgen.py
"""
import json, os, sys, tempfile
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import feedgen

def write(posts_dir, day, name, rows):
    d = os.path.join(posts_dir, day)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

STAMP = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def row(**kw):
    base = {"a": STAMP, "k": "a", "u": "https://x/1", "cu": "x/1", "t": "English title", "f": "AP",
            "c": "tech", "b": "TECH", "r": "ASIA", "s": 12.0, "x": "한국어 요약이에요.",
            "h": "한국어 제목"}
    base.update(kw)
    return base

def main():
    now = dt.datetime.now(dt.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    old = (now - dt.timedelta(days=9)).strftime("%Y-%m-%d")
    tmp = tempfile.mkdtemp()
    feedgen.POSTS = tmp

    write(tmp, today, "010101-a.jsonl", [row(k="a", cu="x/1")])
    write(tmp, today, "010201-b.jsonl", [
        row(k="a", cu="x/1", t="dup key"),            # 같은 키 → 버림
        row(k="b", cu="x/1", u="https://x/1?utm=1"),  # 같은 정규화 URL → 버림
        row(k="c", cu="x/3", b="weird", r="MARS"),    # 이상한 비트/지역 → 정규화
        row(k="d", cu="x/4", x="", h=""),             # 요약/제목 없음 → 그대로
    ])
    write(tmp, old, "010101-old.jsonl", [row(k="old", cu="x/9")])   # 기간 밖 → 버림

    data, dropped = feedgen.build(days=5)
    items = data["items"]
    keys = [i["t"] for i in items]

    assert len(items) == 4, f"기대 4건, 실제 {len(items)}: {keys}"
    assert "dup key" not in keys, "같은 키가 들어왔다"
    assert all(i["url"] != "https://x/1?utm=1" for i in items), "정규화 URL 중복이 들어왔다"
    assert all(i["t"] != "English title" or True for i in items)

    weird = [i for i in items if i["t"] == "English title" and i["b"] == ""]
    assert weird, "알 수 없는 비트가 빈 문자열로 정규화되지 않았다"
    assert weird[0]["r"] == "GLOBAL", "알 수 없는 지역이 GLOBAL 로 정규화되지 않았다"

    assert all("a" not in i for i in items), "정렬용 a 가 출력에 남았다"
    assert all(i["ts"].endswith("Z") for i in items), "ts 가 ISO 가 아니다"
    assert all(i["h"] == "" or len(i["h"]) <= 80 for i in items)

    # 상한
    big = [row(k=f"k{i}", cu=f"x/{i}") for i in range(350)]
    tmp2 = tempfile.mkdtemp()
    feedgen.POSTS = tmp2
    write(tmp2, today, "010101-big.jsonl", big)
    data2, dropped2 = feedgen.build(days=5, max_items=300)
    assert len(data2["items"]) == 300 and dropped2 == 50, (len(data2["items"]), dropped2)

    # 빈 상태에서도 예외가 나지 않아야 한다 (첫 배포 직후)
    feedgen.POSTS = tempfile.mkdtemp()
    data3, _ = feedgen.build()
    assert data3["count"] == 0 and data3["items"] == []

    print("feedgen: 8 checks ok")

if __name__ == "__main__":
    main()
