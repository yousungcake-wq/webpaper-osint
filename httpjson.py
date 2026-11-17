#!/usr/bin/env python3
"""httpjson.py — JSON POST 한 곳.

beats.py·llm.py 가 같은 6줄(+ UA 상수)을 각자 갖고 있었다(2026-11-17 통합).
무료 API 들의 공통 함정이 두 개 있어서, 그 대응도 여기 한 곳에 둔다.
  ① UA 없이 보내면 앞단 Cloudflare 가 1010 으로 403 을 준다(Groq·Cerebras, 2026-11-08).
  ② 타임아웃이 길면 폴백 전체가 회차(5분)를 넘겨 다음 트리거와 충돌한다.
"""
import json
import urllib.request

UA = ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Mobile Safari/537.36")


def post(url, payload, headers=None, timeout=30):
    """POST JSON → dict. 실패는 예외로 올린다(호출부가 폴백을 판단한다)."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())
