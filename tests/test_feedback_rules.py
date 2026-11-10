#!/usr/bin/env python3
"""규칙 회귀 테스트 (네트워크 불필요).  python3 tests/test_feedback_rules.py

2026-11-10 👎 #737(FormatBiz) 계기로 추가한 '잘린 링크' 규칙과,
반응 보정이 게이트에 실제로 걸리는지 확인한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import osint                                                    # noqa: E402

T = "Content Americas 2027 Expands into Kids Entertainment with New Summit"


def test_truncated_url():
    assert osint.junk_item(T, "https://www.formatbiz.it/dettNews.aspx?id") is True
    assert osint.junk_item(T, "https://x.test/a?pid=") is True


def test_normal_url_kept():
    assert osint.junk_item(T, "https://www.formatbiz.it/dettNews.aspx?id=12345&lang=en") is False
    assert osint.junk_item(
        "Nigeria turns to hybrid cloud infrastructure for its postcode database",
        "https://techcabal.com/2026/11/10/nigeria-hybrid-cloud-storage-postcode/") is False


def test_fb_adj_bounded():
    v = osint.fb_adj("없는소스", "없는비트")
    assert v == 0.0
    assert -5.0 <= osint.fb_adj("Guardian", "CONFLICT") <= 2.5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("전부 통과")
