#!/usr/bin/env python3
"""실험: '형식 판정' LLM 한 콜이 정규식 다발이 놓친 오염을 얼마나 잡는가.

최근 게시 212건을 5개 묶음으로 LLM 에 넣고, 각 제목을 KEEP/DROP + 이유코드로 받는다.
정답셋은 (a) 감사 스크립트의 정규식 표본을 내가 눈으로 확정한 목록, (b) LLM 이 새로
찾아낸 것(내가 직접 검수).
"""
import json, glob, os, re, sys

rows = []
for p in sorted(glob.glob('state/posts/*/*.jsonl')):
    for l in open(p):
        l = l.strip()
        if l:
            try: rows.append(json.loads(l))
            except Exception: pass
rows.sort(key=lambda r: r.get('a') or '')
rec = [r for r in rows if (r.get('a') or '') >= '2026-11-11 00:00']
print(f"표본 {len(rec)}건")

RUBRIC = """당신은 국제 뉴스 큐레이터다. 독자는 다음 취향을 가진 한 사람이다.

관심(남긴다):
- 국제정치·안보·분쟁의 '사건'과 그에 대한 '대응·제재·협상'
- 기술·AI·반도체 산업의 실질적 변화(규제, 사고, 인사 스캔들, 공급망)
- 경제·금융의 정책·구조 사건(금리 결정, 관세, 대형 M&A·IPO, 국가 경제 위기)

비관심(버린다):
- MARKET: 일상 시황·지수 등락·종목 추천·"stock market today"·개별 종목 등락 기사
- SPORT: 스포츠 경기·대회·선수
- PROMO: 기업·기관 홍보, 행사 개최/확장, 수상, 보도자료
- FORECAST: 전망·예측·"~할 수 있다"만 있는 분석(사건 없음)
- LOCAL: 한 나라의 지방 정치·의례·연설·지역 행사
- AIDREPORT: 인도지원 상황보고·통계 보고서(UPDATE #12 같은 것)
- LIFE: 종교·연예·생활
- OFFTOPIC: 그 밖에 뉴스 가치가 낮은 것

각 항목에 대해 KEEP 또는 DROP 과 이유코드를 하나 골라 JSON 배열로만 답한다.
형식: [{"i":1,"d":"KEEP","c":"OK"},{"i":2,"d":"DROP","c":"MARKET"}, ...]
애매하면 KEEP 쪽으로 기운다(좋은 기사를 버리는 비용이 더 크다)."""

def make_prompt(batch):
    lines = []
    for i, r in enumerate(batch, 1):
        lines.append(f'{i}. [{(r.get("b") or "?")}/{(r.get("r") or "?")}] {(r.get("t") or "")[:150]}')
    return RUBRIC + "\n\n# 기사 목록\n" + "\n".join(lines) + "\n\nJSON 배열만 출력:"

def run():
    import llm
    B = 40
    out = []
    for s in range(0, len(rec), B):
        batch = rec[s:s + B]
        raw = llm._generate(make_prompt(batch),
                            accept=lambda r: "[" in r and len(r) > 40)
        if not raw:
            print(f"batch {s//B}: 응답 없음"); continue
        m = re.search(r'\[.*\]', raw, re.S)
        if not m:
            print(f"batch {s//B}: JSON 없음 {raw[:200]}"); continue
        try:
            js = json.loads(m.group(0))
        except Exception as e:
            print(f"batch {s//B}: JSON 파싱 실패 {e}"); continue
        for j in js:
            i = j.get("i")
            if isinstance(i, int) and 1 <= i <= len(batch):
                r = batch[i - 1]
                out.append({"a": r["a"], "t": r.get("t"), "b": r.get("b"), "r": r.get("r"),
                            "f": r.get("f"), "d": j.get("d"), "c": j.get("c"),
                            "why": (j.get("w") or j.get("why") or "")[:120]})
        print(f"batch {s//B}: {len(js)}건")
    json.dump(out, open('/tmp/judge.json', 'w'), ensure_ascii=False, indent=1)
    print("저장", len(out))

if __name__ == "__main__":
    run()
