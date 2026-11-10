#!/usr/bin/env python3
"""backfill_head.py — 예전 게시물에 한국어 제목(h)을 채운다 (1회성).

새 게시물은 요약 호출에서 제목을 같이 받지만(llm.summarize_parts), 그 전에 쌓인
232건은 제목이 없다. 사이트에 영어 원문제목이 그대로 뜨는 걸 막으려고 한 번 채운다.
요약문(x)은 건드리지 않고, 틀리면 그냥 건너뛴다(다음 실행에서 다시 시도).

  python3 backfill_head.py --limit 40 --sleep 1.5
  python3 backfill_head.py --dry            # 대상만 센다
"""
import argparse, glob, json, os, re, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import llm

PROMPT = (
    "너는 한국어 뉴스 데스크의 제목 편집자다. 아래 기사의 한국어 제목을 하나만 쓴다.\n"
    "규칙: 28자 이내 · 마침표 없음 · 사건 중심(누가·무엇을·어디서) · 원문제목 직역 금지 · "
    "따옴표·설명·머리기호 없이 제목 한 줄만 출력.\n"
    "예: `EU, AI 규제 2단계 시행령 초안 공개`\n\n"
    "[원문 제목] {t}\n\n[한국어 요약] {x}\n"
)

HAM = re.compile(r"[가-힣]")

def load():
    out = []
    for p in sorted(glob.glob(os.path.join(BASE, "state", "posts", "*", "*.jsonl"))):
        out.append(p)
    return out

def rows_of(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]

def save(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--min-age-min", type=int, default=0)
    a = ap.parse_args()

    todo = []
    for path in load():
        rows = rows_of(path)
        for i, r in enumerate(rows):
            if not (r.get("h") or "").strip() and (r.get("x") or "").strip():
                todo.append((path, i, rows, r))
    todo.sort(key=lambda t: t[3].get("a", ""), reverse=True)     # 최신부터
    print(f"제목 없는 게시물 {len(todo)}건 · 이번에 {min(a.limit, len(todo))}건 시도")
    if a.dry:
        for _, _, _, r in todo[:5]:
            print("  ", r.get("a"), r.get("t", "")[:60])
        return

    done = fail = 0
    for path, idx, rows, r in todo[:a.limit]:
        prompt = PROMPT.format(t=r.get("t", "")[:200], x=(r.get("x") or "")[:600])
        try:
            raw = llm._generate(prompt)
        except Exception as e:
            print("  err:", e)
            raw = ""
        head = llm.clean_head((raw or "").splitlines()[0] if raw else "")
        if not head or len(head) < 6 or not HAM.search(head):
            print(f"  실패: {r.get('t','')[:50]}")
            fail += 1
            time.sleep(a.sleep)
            continue
        cur = rows_of(path)
        cur[idx]["h"] = head[:80]
        save(path, cur)
        done += 1
        print(f"  {head}  ← {r.get('t','')[:55]}")
        time.sleep(a.sleep)
    print(f"완료 {done} · 실패 {fail}")

if __name__ == "__main__":
    main()
