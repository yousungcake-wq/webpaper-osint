#!/usr/bin/env python3
"""프로덕션 분류 프롬프트(beats.PROMPT)로 과거 212건을 다시 판정해 게이트 성능을 잰다.

exp_judge.py 는 내가 손으로 쓴 루브릭이었다. 이건 **실제로 배포된 프롬프트**를 쓴다.
  python3 exp_gate.py  → /tmp/gate_eval.json
"""
import json, glob, sys

sys.path.insert(0, '/var/minis/workspace/wp-audit')
import beats

rows = []
for p in sorted(glob.glob('/var/minis/workspace/wp-audit/state/posts/*/*.jsonl')):
    for l in open(p):
        l = l.strip()
        if l:
            try: rows.append(json.loads(l))
            except Exception: pass
rows.sort(key=lambda r: r.get('a') or '')
rec = [r for r in rows if (r.get('a') or '') >= '2026-11-11 00:00']
print("표본", len(rec))

out = []
B = 24
for s in range(0, len(rec), B):
    batch = rec[s:s + B]
    body = "\n".join(f"{i+1}. {(r.get('t') or '')[:110]}" for i, r in enumerate(batch))
    labs, raw = None, ""
    for attempt in range(3):
        raw = beats._call_text(beats.PROMPT.format(titles=body), max_tokens=2000)
        labs = beats._parse(raw, len(batch)) if raw else None
        if labs:
            break
    if not labs:
        print(f"batch {s//B}: 실패 {raw[:80] if raw else ''}")
        continue
    for r, lab in zip(batch, labs):
        if not lab:
            continue
        out.append({"a": r["a"], "t": r.get("t"), "b": r.get("b"), "f": r.get("f"),
                    "beat": lab[0], "region": lab[1], "form": lab[2], "util": lab[3],
                    "drop": beats.drop_form(lab[2], lab[3])})
    print(f"batch {s//B}: {len(batch)}건")

json.dump(out, open('/tmp/gate_eval.json', 'w'), ensure_ascii=False, indent=1)
n = len(out)
d = sum(1 for x in out if x["drop"])
print(f"판정 {n}건 · 차단 {d} ({d/max(1,n)*100:.1f}%)")
