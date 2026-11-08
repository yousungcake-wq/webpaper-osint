#!/usr/bin/env python3
"""llm.py — 무료 LLM API로 기사 요약을 만든다 (모델 채팅 호출 없이).

지원: GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY (있는 것부터 사용)

  python3 llm.py --json queue/123.json        # 요약문 출력
  python3 llm.py --json queue/123.json --dry  # 프롬프트만 확인
"""
import argparse, json, os, re, sys, time
import urllib.request

SYSTEM = (
    "너는 국제 뉴스 전문 브리퍼다. 주어진 기사로 한국어 요약을 쓴다.\n"
    "규칙:\n"
    "- 정확히 2문장(최대 3문장), 전체 220자 이내. 반드시 해요체(~했어요/~예요).\n"
    "- 첫 문장은 핵심 사실 하나: 누가 무엇을 했고, 결과가 무엇인지.\n"
    "- 두 번째 문장에 가장 중요한 수치나 인용 하나만 덧붙인다.\n"
    "- 기사에 없는 내용은 절대 쓰지 않는다. 추측 금지.\n"
    "- 원문 제목을 그대로 옮기지 않는다. 제목·머리말·출처 목록은 쓰지 않는다.\n"
    "- 인용은 꼭 필요할 때만 한 곳, 큰따옴표로.\n"
    "- 숫자 단위는 한국어로: percent→%, billion→억 달러, million→만 등.\n"
    "- 출력은 본문 문단만. 설명·머리기호·영어 금지."
)


def prompt_for(title, text, thin=False):
    if thin:
        return (f"{SYSTEM}\n\n[기사 제목] {title}\n\n[기사 요약문(짧음)]\n{text[:800]}\n\n"
                "본문을 못 가져와 위 스니펫만 있다. 스니펫에 적힌 사실만으로 2문장 한국어 요약을 써라. "
                "숫자·인용·배경은 스니펫에 있는 것만 쓰고, 추측으로 채우지 마라. 180자를 넘기지 마라.")
    return (f"{SYSTEM}\n\n[기사 제목] {title}\n\n[기사 본문]\n{text[:5000]}\n\n"
            "위 기사로 2문장 한국어 요약을 써라. 220자를 넘기지 마라. "
            "직함·직책은 원문 그대로 옮겨라(Chair=의장, governor=이사, minister=장관). "
            "원문에 없는 수치나 직함을 만들어내지 마라.")


UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36")


def _post(url, payload, headers, timeout=90):
    # UA 없이 보내면 Groq(Cerebras 등)가 Cloudflare 1010 으로 403 을 준다.
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


GEMINI_MODELS = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-3.1-flash-lite"]


def call_gemini(key, prompt, model=None):
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    last = None
    for m in ([model] if model else GEMINI_MODELS):
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
               f"?key={key}")
        try:
            r = _post(url, body, {})
            return r["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            last = e
            if "401" in str(e) or "403" in str(e):
                raise
            continue
    raise last


GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]


def call_groq(key, prompt, model=None):
    url = "https://api.groq.com/openai/v1/chat/completions"
    last = None
    for m in ([model] if model else GROQ_MODELS):
        body = {"model": m, "temperature": 0.4, "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}]}
        try:
            r = _post(url, body, {"Authorization": f"Bearer {key}"})
            return r["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            if "401" in str(e):
                raise
            continue
    raise last


def call_openrouter(key, prompt, model="google/gemini-2.0-flash-exp:free"):
    url = "https://openrouter.ai/api/v1/chat/completions"
    body = {"model": model, "temperature": 0.4, "max_tokens": 700,
            "messages": [{"role": "user", "content": prompt}]}
    r = _post(url, body, {"Authorization": f"Bearer {key}",
                          "HTTP-Referer": "https://nanomuse.cn"})
    return r["choices"][0]["message"]["content"]


def call_cerebras(key, prompt, model="gpt-oss-120b"):
    url = "https://api.cerebras.ai/v1/chat/completions"
    body = {"model": model, "temperature": 0.4, "max_tokens": 700,
            "messages": [{"role": "user", "content": prompt}]}
    r = _post(url, body, {"Authorization": f"Bearer {key}"})
    return r["choices"][0]["message"]["content"]


def call_mistral(key, prompt, model="mistral-small-latest"):
    url = "https://api.mistral.ai/v1/chat/completions"
    body = {"model": model, "temperature": 0.4, "max_tokens": 700,
            "messages": [{"role": "user", "content": prompt}]}
    r = _post(url, body, {"Authorization": f"Bearer {key}"})
    return r["choices"][0]["message"]["content"]


PROVIDERS = [
    ("GEMINI_API_KEY", call_gemini),
    ("CEREBRAS_API_KEY", call_cerebras),
    ("MISTRAL_API_KEY", call_mistral),
    ("GROQ_API_KEY", call_groq),
    ("OPENROUTER_API_KEY", call_openrouter),
]


_COOLDOWN = {}          # name -> epoch 까지 건너뛴다 (한도 초과 반복 호출 방지)


def _cooling(name, seconds=1800):
    until = _COOLDOWN.get(name, 0)
    return until > time.time()


def available():
    return [name for name, _ in PROVIDERS if os.environ.get(name)]


END_OK = '.!?"”’…)~'


def is_complete(s, min_len=80):
    """생성이 중간에 끊겼는지 판정. 끊긴 요약은 게시하지 않는다.

    2026-11-07 실제 사고: 모델이 90자에서 끊긴 응답을 냈고 그대로 채널에 나갔다.
    """
    s = (s or "").strip()
    if len(s) < min_len:
        return False
    return s[-1] in END_OK


def summarize(title, text, dry=False, thin=False):
    prompt = prompt_for(title, text, thin)
    min_len = 60 if thin else 80
    if dry:
        return prompt
    for attempt in range(2):
        for name, fn in PROVIDERS:
            key = os.environ.get(name, "").strip()
            if not key or _cooling(name):
                continue
            try:
                out = fn(key, prompt)
                out = re.sub(r"\s+", " ", out).strip()
                out = re.sub(r"^(요약[:：]?|다음은|위 기사는)\s*", "", out)
                if len(out) > 320:                # 길면 문장 경계에서 자른다
                    cut = max(out.rfind("다. ", 0, 320), out.rfind("요. ", 0, 320))
                    if cut > 80:
                        out = out[:cut + 2]
                if is_complete(out, min_len):
                    # 모델이 놓친 평서체(…습니다)를 해요체로 강제 정규화
                    try:
                        import writer
                        out = writer.korean_smooth(out)
                    except Exception:
                        pass
                    return out
                print(f"[{name}] too short: {out[:40]}", flush=True)
            except Exception as e:
                msg = str(e)
                if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
                    _COOLDOWN[name] = time.time() + 1800      # 30분간 건너뛴다
                print(f"[{name}] {type(e).__name__}: {msg[:120]}", flush=True)
        if attempt == 0:
            import time
            time.sleep(4)
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--title", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    title, text = a.title, a.text
    if a.json:
        d = json.load(open(a.json, encoding="utf-8"))
        title, text = d["title"], d["text"]
    print(f"[providers] {available() or 'none'}", file=sys.stderr)
    print(summarize(title, text, dry=a.dry))


if __name__ == "__main__":
    main()
