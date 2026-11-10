#!/usr/bin/env python3
"""NVIDIA NIM(integrate.api.nvidia.com) 실측 — 우리 파이프라인 프롬프트 그대로 돌린다.

  export NVIDIA_API_KEY=nvapi-...
  python3 tests/test_nvidia.py            # 모델 목록 + 요약 2건 + 분류 1콜
  python3 tests/test_nvidia.py --models   # 쓸 만한 모델만 골라 보기

확인하는 것
  1) 키가 살아 있는가 (/v1/models)
  2) 우리 요약 프롬프트(제목+요약 동시 생성)를 지키는가 · 지연 · 글자수
  3) 비트·지역 분류 프롬프트("번호:비트:지역")를 지키는가
NVIDIA NIM 은 OpenAI 호환이라 base URL 만 바꾸면 llm.py 에 꽂을 수 있다.
"""
import argparse, json, os, re, sys, time
import urllib.request

BASE = "https://integrate.api.nvidia.com/v1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm, beats

# 2026-11-10 실측: 목록에 있어도 404 인 모델이 많다(계정에 프로비저닝 안 됨).
# 아래는 실제로 응답한 것들. reasoning 계열은 max_tokens 를 넉넉히 줘야 content 가 나온다.
CANDIDATES = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "z-ai/glm-5.3-flash",
    "mistralai/mistral-large-4-1t-a52b",
]

SAMPLE_TITLE = "Nigeria's Dangote refinery ships 208,000 tonnes of petrol in eight days"
SAMPLE_TEXT = (
    "Aliko Dangote's 650,000 barrel-per-day refinery has shipped 208,000 tonnes of petrol "
    "in the past eight days, according to shipping records reviewed on Monday. The cargoes, "
    "loaded at the Lekki terminal near Lagos, were carried by five tankers bound for markets "
    "in West Africa and as far as Rotterdam. The plant has been ramping up since August after "
    "years of delays and cost overruns, and executives said it is now running at about 60% of "
    "nameplate capacity. Analysts at Lagos-based Cordros Research estimate the refinery could "
    "cut Nigeria's fuel import bill by $2 billion a year if it sustains output above 400,000 bpd. "
    "The country still imports most of its refined products, a legacy of decades of underinvestment "
    "in domestic refining. Dangote Group said it plans to list the refinery on the Nigerian Exchange "
    "by early next year, a move bankers value at roughly $20 billion."
)

CLASSIFY_TITLES = [
    "Qatar PM joins G7 talks on Iran after Trump meeting",
    "Oil slides as Trump signals de-escalation with Iran",
    "Anthropic pulls enterprise offer after leak",
    "Parliament passes budget bill after marathon session",
    "Taylor Swift adds 12 dates to stadium tour",
]


def call(model, messages, max_tokens=1400, temperature=0.4, timeout=120):
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    req = urllib.request.Request(
        f"{BASE}/chat/completions", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    msg = d["choices"][0]["message"]
    call.last_usage = d.get("usage") or {}
    return msg.get("content") or ""


def get_models():
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    req = urllib.request.Request(f"{BASE}/models",
                                 headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return [m["id"] for m in json.loads(r.read().decode()).get("data", [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--limit", type=int, default=2)
    a = ap.parse_args()

    if not os.environ.get("NVIDIA_API_KEY", "").strip():
        print("NVIDIA_API_KEY 가 없다.  Settings → 환경 변수 에서 추가할 것.")
        return 1

    try:
        models = get_models()
    except Exception as e:
        print("모델 목록 실패:", type(e).__name__, str(e)[:200])
        return 1
    print(f"모델 {len(models)}개")
    if a.models:
        for m in models:
            if any(k in m.lower() for k in ("instruct", "nemotron", "chat", "qwen", "deepseek", "llama")):
                print("  ", m)
        return 0

    use = [m for m in CANDIDATES if m in models] or [m for m in models if "instruct" in m][:a.limit]
    print("시험 모델:", use[:a.limit])

    # 1) 요약 + 제목
    prompt = llm.prompt_for(SAMPLE_TITLE, SAMPLE_TEXT, head=True)
    for m in use[:a.limit]:
        t0 = time.time()
        try:
            out = call(m, [{"role": "user", "content": prompt}])
        except Exception as e:
            print(f"\n[{m}] 실패: {type(e).__name__} {str(e)[:160]}")
            continue
        dt = time.time() - t0
        head, body = llm.split_head(out)
        u = getattr(call, "last_usage", {}) or {}
        print(f"\n[{m}] {dt:.1f}s · {len(out)}자 · 토큰 in {u.get('prompt_tokens')} / "
              f"out {u.get('completion_tokens')} (추론 {u.get('completion_tokens_details',{}).get('reasoning_tokens')})")
        print("  제목:", repr(head))
        print("  요약:", body[:220])
        print("  완결성:", llm.is_complete(body, 80))

    # 2) 분류
    body_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(CLASSIFY_TITLES))
    prompt = beats.PROMPT.format(titles=body_text)
    m = use[0]
    t0 = time.time()
    try:
        out = call(m, [{"role": "user", "content": prompt}], max_tokens=400, temperature=0)
    except Exception as e:
        print(f"\n[{m}] 분류 실패: {type(e).__name__} {str(e)[:160]}")
        return 0
        return 0
    print(f"\n[{m}] 분류 {time.time() - t0:.1f}s")
    print(out.strip()[:400])
    print("파싱:", beats._parse(out, len(CLASSIFY_TITLES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
