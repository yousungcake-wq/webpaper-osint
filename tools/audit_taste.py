#!/usr/bin/env python3
"""최근 게시물을 '취향 오염' 유형별로 분류해 실측한다."""
import json, glob, re, collections, sys

rows = []
for p in sorted(glob.glob('state/posts/*/*.jsonl')):
    for l in open(p):
        l = l.strip()
        if l:
            try: rows.append(json.loads(l))
            except Exception: pass
rows.sort(key=lambda r: r.get('a') or '')

SINCE = sys.argv[1] if len(sys.argv) > 1 else '2026-11-11 00:00'
rec = [r for r in rows if (r.get('a') or '') >= SINCE]
print(f"표본 {len(rec)}건  {rec[0]['a']} ~ {rec[-1]['a']}\n")

P = {
 '시황·종목노이즈': re.compile(r"(?i)(stock market today|stocks? (set|poised|rise|fall|dip|gain|rally|surge)|"
    r"nasdaq|s&p 500|dow (jones|futures)|shares (up|down|rise|fall)|market (wrap|report|today)|"
    r"stock dips|stock gains|key facts|bargain or a|valuation|analyst warns|"
    r"top gainers|futures (rise|fall)|gains? more than \d|closing bell|"
    r"what to (buy|watch)|investors? into|ipo outlook|retail buying)"),
 '스포츠': re.compile(r"(?i)(afcon|qualifier|line-?up against|national team|u-?2[0-3]|world cup|"
    r"premier league|la liga|serie a|champions league|olympic|grand prix|motor racing|"
    r"formula one|nascar|motogp|tennis|golf|boxing|fifa|caf\b|uefa)"),
 '홍보·행사': re.compile(r"(?i)(at the european parliament|championing|take center stage|"
    r"innovation (summit|awards)|provides? real-time|interfaith forum|"
    r"expands? into|new summit and pavilion|line-?up|showcase|"
    r"\bsummits?\b.*\b(brings?|talks)\b|press release|announces? )"),
 '인도지원 상황보고': re.compile(r"(?i)(update #\d|situation report|reliefweb|"
    r"humanitarian (fund|response plan|update)|displacement caused by|"
    r"reports? \d+ aid workers)"),
 '나이지리아·지방정치': re.compile(r"(?i)(tinubu|naira|jigawa|ibom|igp orders|"
    r"governor said|rally soundbites|\bcsos?\b tells|premium times)"),
 '예측·전망': re.compile(r"(?i)(could (lead|draw|reshape)|if .* would|what if|"
    r"analysts? (say|expect)|outlook|forecast|is it time to|should you)"),
 '중복사건(Nvidia)': re.compile(r"(?i)nvidia"),
}
CAT = collections.Counter()
hits = collections.defaultdict(list)
for r in rec:
    t = (r.get('t') or '') + ' ' + (r.get('h') or '')
    matched = False
    for k, rx in P.items():
        if k == '중복사건(Nvidia)':
            continue
        if rx.search(t):
            CAT[k] += 1; hits[k].append(r); matched = True
    if not matched:
        CAT['그 외'] += 1

print("■ 유형별 (중복 카운트 허용)")
for k, v in CAT.most_common():
    print(f"  {k:<18} {v:>4}  {v/len(rec)*100:>5.1f}%")

print("\n■ 소스 표기 이상 (피드명이 URL)")
bad_src = [r for r in rec if (r.get('f') or '').startswith('http')]
print(f"  {len(bad_src)}건: " + ", ".join(sorted({r['f'] for r in bad_src})))

print("\n■ 비트/지역 분포")
for k in ('b', 'r'):
    c = collections.Counter(r.get(k) or '-' for r in rec)
    print(f"  {k}: " + "  ".join(f"{a}={b}" for a, b in c.most_common(9)))

nv = [r for r in rec if P['중복사건(Nvidia)'].search(r.get('t') or '')]
print(f"\n■ Nvidia 단일 사건 반복: {len(nv)}건 ({len(nv)/len(rec)*100:.1f}%)")
for r in nv[-6:]:
    print("   ", r['a'][5:16], (r.get('t') or '')[:70])

for k in ('시황·종목노이즈', '스포츠', '홍보·행사', '인도지원 상황보고', '나이지리아·지방정치'):
    print(f"\n■ {k} 예시")
    for r in hits[k][-5:]:
        print("   ", r['a'][5:16], (r.get('f') or '')[:14].ljust(14), (r.get('t') or '')[:66])
