# -*- coding: utf-8 -*-
"""測試目前使用的字元級字幕分句邏輯。"""
import sys

sys.path.insert(0, ".")

from backend.asr_engine import ASREngine


def make_chars(text, start, end):
    duration = max(0.0, end - start)
    step = duration / max(1, len(text))
    return [
        {
            "start": start + index * step,
            "end": start + (index + 1) * step,
            "text": char,
        }
        for index, char in enumerate(text)
    ]


engine = ASREngine.__new__(ASREngine)
engine.on_progress = lambda percent, message: None

text = (
    "專注產業透視未來歡迎來到車未來。今天我跟創辦人小七，"
    "我們一起來到位於彰化的車輛安全審驗中心VSCC。今天很高興能夠訪問到我們的執行長。"
)
sentences = engine.build_sentences_from_chars(
    chars=make_chars(text, 0.0, 57.0),
    diar_segments=[],
    to_traditional=False,
)
for sentence in sentences:
    print(f"[{sentence['start']:.1f} -> {sentence['end']:.1f}] {sentence['text']}")

assert "".join(sentence["text"] for sentence in sentences) == text
assert len(sentences) == 3


long_text = "這是一段完全沒有標點符號的很長的文字內容用來測試強制切斷功能是否正常運作當字數超過五十個字的時候應該要被強制切斷才對否則字幕會太長不適合閱讀"
long_sentences = engine.build_sentences_from_chars(
    chars=make_chars(long_text, 0.0, 100.0),
    diar_segments=[],
    to_traditional=False,
)
print("\n--- 無標點測試 ---")
for sentence in long_sentences:
    print(
        f"[{sentence['start']:.1f} -> {sentence['end']:.1f}] "
        f"({len(sentence['text'])}字) {sentence['text']}"
    )

assert "".join(sentence["text"] for sentence in long_sentences) == long_text
assert all(len(sentence["text"]) <= 50 for sentence in long_sentences)

print("\n✅ 分句測試完成！")
