# -*- coding: utf-8 -*-
"""測試句子級別語者分派邏輯"""
import sys
sys.path.insert(0, ".")
from backend.asr_engine import ASREngine

engine = ASREngine.__new__(ASREngine)
engine.on_progress = lambda p, m: None

class MockTimestamp:
    def __init__(self, text, start_time, end_time):
        self.text = text
        self.start_time = start_time
        self.end_time = end_time

class MockResult:
    def __init__(self, text, time_stamps):
        self.text = text
        self.time_stamps = time_stamps

# ====================================================
# 測試 1：兩個語者各說一句（句末標點分句）
# ====================================================
print("=== 測試 1: 句末標點分句 ===")
asr_results = [MockResult(
    text="你好啊你是誰。晚餐吃什麼呢？",
    time_stamps=[
        MockTimestamp("你", 0.0, 0.3),
        MockTimestamp("好", 0.3, 0.6),
        MockTimestamp("啊", 0.6, 0.9),
        MockTimestamp("你", 0.9, 1.2),
        MockTimestamp("是", 1.2, 1.5),
        MockTimestamp("誰", 1.5, 1.8),   # 標點 。 由 Step1 附加到此
        # Step1 還原後 "誰" -> "誰。"
        MockTimestamp("晚", 2.0, 2.5),
        MockTimestamp("餐", 2.5, 3.0),
        MockTimestamp("吃", 3.0, 3.5),
        MockTimestamp("什", 3.5, 3.8),
        MockTimestamp("麼", 3.8, 4.2),
        MockTimestamp("呢", 4.2, 4.8),   # 標點 ？ 由 Step1 附加到此
    ]
)]

diar_segments = [
    {"start": 0.0, "end": 1.9, "speaker": "SPEAKER_01"},
    {"start": 2.0, "end": 5.0, "speaker": "SPEAKER_02"},
]

result, chars = engine.merge(asr_results, diar_segments, to_traditional=False)
for seg in result:
    print(f"  [{seg['start']:.1f} -> {seg['end']:.1f}] {seg['speaker']}: {seg['text']}")
print(f"  共 {len(result)} 個片段")

# 原始 text 有 。 但 timestamps 裡沒有獨立的 。 token
# Step1 會把 。附加到 "誰" 變成 "誰。"，把 ？附加到 "呢" 變成 "呢？"
# 這樣在 Step2 會在 "誰。" 處斷句
# 句子1: "你好啊你是誰。" (0.0-1.8) -> SPEAKER_01 重疊更多
# 句子2: "晚餐吃什麼呢？" (2.0-4.8) -> SPEAKER_02 重疊更多

# ====================================================
# 測試 2：跨語者長句（逗號補切）
# ====================================================
print("\n=== 測試 2: 跨語者長文，逗號補切 ===")

# 建構一個 30+ 字的文本，中間有逗號
text2 = "我們今天要討論的是關於車輛安全審驗的相關規範，接下來請執行長說明一下具體的內容？"
chars2 = []
t = 0.0
for ch in text2:
    if ch in "，？":
        # 標點附加到前一個 char（模擬 Step1 還原後）
        if chars2:
            chars2[-1] = MockTimestamp(chars2[-1].text + ch, chars2[-1].start_time, chars2[-1].end_time)
        continue
    chars2.append(MockTimestamp(ch, t, t + 0.3))
    t += 0.3

asr_results2 = [MockResult(text=text2, time_stamps=chars2)]

# SPEAKER_01 講前半 (0-5s), SPEAKER_02 講後半 (5-12s)
mid_time = t / 2
diar_segments2 = [
    {"start": 0.0, "end": mid_time, "speaker": "SPEAKER_01"},
    {"start": mid_time, "end": t + 1.0, "speaker": "SPEAKER_02"},
]

result2, chars2_out = engine.merge(asr_results2, diar_segments2, to_traditional=False)
for seg in result2:
    print(f"  [{seg['start']:.1f} -> {seg['end']:.1f}] {seg['speaker']}: {seg['text']}")
print(f"  共 {len(result2)} 個片段")

# ====================================================
# 測試 3：語者歸組 (group_by_speaker)
# ====================================================
print("\n=== 測試 3: group_by_speaker 歸組 ===")

test_sentences = [
    {"start": 0.0, "end": 1.5, "speaker": "A", "text": "第一句，"},
    {"start": 1.6, "end": 3.0, "speaker": "A", "text": "第二句。"},
    {"start": 3.5, "end": 5.0, "speaker": "B", "text": "第三句。"},
    {"start": 5.5, "end": 7.0, "speaker": "A", "text": "第四句。"},
    {"start": 7.1, "end": 8.0, "speaker": "A", "text": "第五句。"},
    # 大間隔
    {"start": 20.0, "end": 22.0, "speaker": "A", "text": "長間隔後。"},
]

groups = ASREngine.group_by_speaker(test_sentences)
for g in groups:
    print(f"  [{g['speaker']}] ({g['start']:.1f} -> {g['end']:.1f}): {g['combined_text']}")
    print(f"    內含 {len(g['segments'])} 個子句")
print(f"  共 {len(groups)} 個歸組段落")

assert len(groups) == 4, f"預期 4 個歸組段落，得到 {len(groups)}"
assert groups[0]["speaker"] == "A"
assert len(groups[0]["segments"]) == 2  # 第一句 + 第二句
assert groups[1]["speaker"] == "B"
assert groups[2]["speaker"] == "A"
assert len(groups[2]["segments"]) == 2  # 第四句 + 第五句
assert groups[3]["speaker"] == "A"  # 長間隔後的單獨段落
assert len(groups[3]["segments"]) == 1

print("\n✅ 測試完成！")
