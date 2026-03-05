# -*- coding: utf-8 -*-
"""測試 split_sentences 分句邏輯"""
import sys
sys.path.insert(0, ".")
from backend.asr_engine import ASREngine

segments = [{
    "start": 0.0,
    "end": 57.0,
    "speaker": "SPEAKER_01",
    "text": "專注產業透視未來歡迎來到車未來。今天我跟創辦人小七，我們一起來到位於彰化的車輛安全審驗中心VSCC。今天很高興能夠訪問到我們的執行長。"
}]

sentences = ASREngine.split_sentences(segments)
for s in sentences:
    print(f"[{s['start']:.1f} -> {s['end']:.1f}] {s['text']}")

print(f"\nTotal: {len(sentences)} sentences")

# 測試無標點的長文
segments2 = [{
    "start": 0.0,
    "end": 100.0,
    "text": "這是一段完全沒有標點符號的很長的文字內容用來測試強制切斷功能是否正常運作當字數超過五十個字的時候應該要被強制切斷才對否則字幕會太長不適合閱讀",
}]

sentences2 = ASREngine.split_sentences(segments2)
print("\n--- 無標點測試 ---")
for s in sentences2:
    print(f"[{s['start']:.1f} -> {s['end']:.1f}] ({len(s['text'])}字) {s['text']}")
