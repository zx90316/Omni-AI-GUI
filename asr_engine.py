# -*- coding: utf-8 -*-
"""
ASR 核心引擎
封裝 Qwen ASR 轉錄、語者分離、合併邏輯，支援 GPU/CPU 與多模型切換。

重型 ML 套件（qwen_asr, opencc, pyannote）採用延遲匯入，
讓 GUI 能快速啟動。
"""
import bisect
import time
import unicodedata
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional


def _is_punctuation(ch: str) -> bool:
    """判斷字元是否為標點符號（中英文皆涵蓋）"""
    if ch in '，。！？、；：＂＇（）《》【】…—·,.:;!?\'"()[]{}~@#$%^&*+-=/<>':
        return True
    cat = unicodedata.category(ch)
    return cat.startswith("P")


def _get_cc():
    """延遲載入繁簡轉換器"""
    from opencc import OpenCC
    return OpenCC('s2twp')


def detect_device() -> dict:
    """偵測可用裝置，回傳 device/dtype 設定"""
    import torch
    if torch.cuda.is_available():
        return {"device": "cuda:0", "dtype": torch.bfloat16, "label": "CUDA GPU"}
    else:
        return {"device": "cpu", "dtype": torch.float32, "label": "CPU"}


class ASREngine:
    """
    ASR 引擎，整合轉錄、語者分離、合併功能。

    Args:
        model_name: HuggingFace 模型名稱
        device: "auto" / "cuda:0" / "cpu"
        on_progress: 進度回呼 (percent: float, message: str)
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-ASR-1.7B",
        device: str = "auto",
        on_progress: Optional[Callable[[float, str], None]] = None,
    ):
        self.model_name = model_name
        self.on_progress = on_progress or (lambda p, m: None)
        self._model = None

        # 裝置設定
        import torch
        if device == "auto":
            info = detect_device()
            self.device = info["device"]
            self.dtype = info["dtype"]
            self.device_label = info["label"]
        elif device == "cpu":
            self.device = "cpu"
            self.dtype = torch.float32
            self.device_label = "CPU"
        else:
            self.device = device
            self.dtype = torch.bfloat16
            self.device_label = device

    def _progress(self, percent: float, message: str):
        """更新進度"""
        self.on_progress(percent, message)
        print(f"[{percent:.0f}%] {message}")

    # ============================================
    # 模型管理
    # ============================================

    def load_model(self):
        """載入 ASR 模型"""
        if self._model is not None:
            return

        from qwen_asr import Qwen3ASRModel
        from config import FORCED_ALIGNER

        self._progress(5, f"載入模型 {self.model_name}...")
        self._model = Qwen3ASRModel.from_pretrained(
            self.model_name,
            dtype=self.dtype,
            device_map=self.device,
            max_inference_batch_size=32,
            max_new_tokens=2048,
            forced_aligner=FORCED_ALIGNER,
            forced_aligner_kwargs=dict(
                dtype=self.dtype,
                device_map=self.device,
            ),
        )
        self._progress(15, "模型載入完成")

    def unload_model(self):
        """釋放模型記憶體"""
        if self._model is not None:
            import torch
            del self._model
            self._model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    # ============================================
    # 音訊分段
    # ============================================

    def split_audio_by_silence(
        self,
        audio_path: str,
        target_duration: float = 120.0,
        max_duration: float = 180.0,
        silence_threshold_db: float = -40.0,
        min_silence_duration: float = 0.3,
        frame_duration: float = 0.02,
    ) -> List[tuple]:
        """依靜音段切分音訊，回傳各段 (start_sec, end_sec) 清單"""
        import numpy as np
        import soundfile as sf
        from audio_utils import get_audio_duration

        total_duration = get_audio_duration(audio_path)

        if total_duration <= target_duration:
            return [(0.0, total_duration)]

        self._progress(18, f"偵測靜音段... (音訊長度: {total_duration:.1f}s)")

        data, sr = sf.read(audio_path, dtype='float32')
        if data.ndim > 1:
            data = data.mean(axis=1)

        frame_size = int(sr * frame_duration)
        num_frames = len(data) // frame_size
        threshold_linear = 10 ** (silence_threshold_db / 20.0)

        # 計算每個 frame 的 RMS
        rms_values = []
        for i in range(num_frames):
            frame = data[i * frame_size : (i + 1) * frame_size]
            rms = np.sqrt(np.mean(frame ** 2))
            rms_values.append(rms)

        # 偵測靜音區段
        silence_regions = []
        in_silence = False
        silence_start = 0

        for i, rms in enumerate(rms_values):
            time_sec = i * frame_duration
            if rms < threshold_linear:
                if not in_silence:
                    in_silence = True
                    silence_start = time_sec
            else:
                if in_silence:
                    silence_end = time_sec
                    if silence_end - silence_start >= min_silence_duration:
                        silence_regions.append((silence_start, silence_end))
                    in_silence = False

        if in_silence:
            silence_end = num_frames * frame_duration
            if silence_end - silence_start >= min_silence_duration:
                silence_regions.append((silence_start, silence_end))

        # 依靜音段選擇切分點
        chunks = []
        current_start = 0.0

        while current_start < total_duration:
            ideal_end = current_start + target_duration

            if ideal_end >= total_duration:
                chunks.append((current_start, total_duration))
                break

            search_start = max(current_start + 60.0, ideal_end - 30.0)
            search_end = min(total_duration, current_start + max_duration)

            best_split = None
            best_distance = float("inf")

            for s_start, s_end in silence_regions:
                mid = (s_start + s_end) / 2
                if search_start <= mid <= search_end:
                    dist = abs(mid - ideal_end)
                    if dist < best_distance:
                        best_distance = dist
                        best_split = mid

            if best_split is not None:
                chunks.append((current_start, best_split))
                current_start = best_split
            else:
                forced_end = min(current_start + max_duration, total_duration)
                chunks.append((current_start, forced_end))
                current_start = forced_end

        self._progress(20, f"分段完成：{len(chunks)} 個片段")
        return chunks

    # ============================================
    # ASR 轉錄
    # ============================================

    def _transcribe_single(self, audio_path: str, language: str = "Chinese"):
        """單段 ASR 轉錄"""
        results = self._model.transcribe(
            audio=[str(audio_path)],
            language=[language],
            return_time_stamps=True,
        )
        return results

    def transcribe(
        self,
        audio_path: str,
        language: str = "Chinese",
        target_duration: float = 120.0,
    ) -> list:
        """
        ASR 轉錄（自動處理長音訊分段）

        Args:
            audio_path: WAV 音訊路徑
            language: 語言
            target_duration: 切分目標長度（秒）

        Returns:
            ASR 結果列表（時間戳已偏移修正）
        """
        import soundfile as sf
        from audio_utils import get_audio_duration

        total_duration = get_audio_duration(str(audio_path))

        # 短音訊直接整段處理
        if total_duration <= target_duration:
            self._progress(25, "短音訊，直接整段轉錄...")
            results = self._transcribe_single(str(audio_path), language)
            self._progress(60, f"轉錄完成，共 {len(results)} 段")
            return results

        # 長音訊分段處理
        chunks = self.split_audio_by_silence(str(audio_path), target_duration=target_duration)
        all_results = []
        result_dir = Path(audio_path).parent
        full_data, sr = sf.read(str(audio_path), dtype='float32')

        for i, (chunk_start, chunk_end) in enumerate(chunks):
            progress = 20 + (i / len(chunks)) * 40
            self._progress(progress, f"轉錄片段 {i+1}/{len(chunks)}: {chunk_start:.1f}s → {chunk_end:.1f}s")

            # 切片寫出 WAV
            chunk_path = result_dir / f"chunk_{i}.wav"
            start_sample = int(chunk_start * sr)
            end_sample = int(chunk_end * sr)
            chunk_data = full_data[start_sample:end_sample]
            sf.write(str(chunk_path), chunk_data, sr)

            # ASR 轉錄
            chunk_results = self._transcribe_single(str(chunk_path), language)

            # 修正時間戳偏移
            for r in chunk_results:
                if hasattr(r, 'time_stamps') and r.time_stamps:
                    new_timestamps = []
                    for ts in r.time_stamps:
                        kwargs = {}
                        if hasattr(ts, 'start_time') and ts.start_time is not None:
                            kwargs['start_time'] = ts.start_time + chunk_start
                        if hasattr(ts, 'end_time') and ts.end_time is not None:
                            kwargs['end_time'] = ts.end_time + chunk_start
                        if kwargs:
                            new_timestamps.append(dc_replace(ts, **kwargs))
                        else:
                            new_timestamps.append(ts)
                    r.time_stamps = new_timestamps

            all_results.extend(chunk_results)

            # 清理暫存檔
            try:
                chunk_path.unlink()
            except Exception:
                pass

        self._progress(60, f"轉錄完成，共 {len(all_results)} 段")
        return all_results

    # ============================================
    # 語者分離
    # ============================================

    def diarize(self, audio_path: str) -> List[Dict]:
        """執行語者分離"""
        self._progress(62, "載入語者分離模型...")

        import torch
        from pyannote.audio import Pipeline
        from audio_utils import load_audio
        from config import DIARIZATION_MODEL, HF_TOKEN

        pipeline = Pipeline.from_pretrained(
            DIARIZATION_MODEL,
            token=HF_TOKEN or None,
        )

        device = "cuda" if torch.cuda.is_available() else "cpu"
        pipeline.to(torch.device(device))

        self._progress(68, "執行語者分離...")
        waveform, sample_rate = load_audio(str(audio_path))
        output = pipeline({"waveform": waveform, "sample_rate": sample_rate})

        diarization_result = output.speaker_diarization
        diar_segments = []
        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            diar_segments.append({
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker,
            })

        self._progress(78, f"語者分離完成，{len(diar_segments)} 個區段")
        return diar_segments

    # ============================================
    # 合併 ASR + 語者分離
    # ============================================

    def merge(
        self,
        asr_results,
        diar_segments: List[Dict],
        gap_threshold: float = 1.0,
        to_traditional: bool = True,
    ) -> List[Dict[str, Any]]:
        """合併 ASR 字元級對齊與語者分離結果"""
        self._progress(80, "合併 ASR 與語者分離結果...")

        if not diar_segments:
            # Inject a dummy speaker segment so timestamp extraction still runs
            diar_segments = [{"start": 0.0, "end": 999999.0, "speaker": "UNKNOWN"}]

        # Step 1: 取出所有字元時間戳，並從原文還原標點符號
        chars = []
        for r in asr_results:
            if not hasattr(r, "time_stamps") or not r.time_stamps:
                continue

            # 取得對齊字元列表
            aligned_chars = []
            for ts in r.time_stamps:
                char_text = getattr(ts, "text", "")
                start_time = getattr(ts, "start_time", None)
                end_time = getattr(ts, "end_time", None)
                if start_time is None or end_time is None:
                    continue
                aligned_chars.append({
                    "text": char_text,
                    "start": float(start_time),
                    "end": float(end_time),
                })

            # 從原文 text 還原標點符號
            # 對齊字元可能是多字元 token（如 VSSC），需用子索引追蹤
            original_text = getattr(r, "text", "") or ""
            if original_text and aligned_chars:
                ai = 0   # aligned_chars 指標
                si = 0   # 當前 aligned token 內的子索引
                mismatch_count = 0  # 連續不匹配計數

                trailing = False  # 是否已進入尾部追蹤模式

                for ch in original_text:
                    if ai >= len(aligned_chars):
                        # 所有 token 已匹配完，只處理緊接的尾部標點
                        if not trailing:
                            trailing = True
                        if trailing and _is_punctuation(ch):
                            aligned_chars[-1]["text"] += ch
                        elif trailing:
                            break  # 遇到非標點即停止
                        continue

                    token_text = aligned_chars[ai]["text"]

                    if si < len(token_text) and ch == token_text[si]:
                        # 匹配到當前 token 內的字元
                        si += 1
                        mismatch_count = 0
                        if si >= len(token_text):
                            # 整個 token 匹配完成，移到下一個
                            ai += 1
                            si = 0
                    elif _is_punctuation(ch):
                        # 只在同步狀態下附加標點（連續不匹配 < 1 即停止）
                        if mismatch_count == 0 and ai > 0:
                            aligned_chars[ai - 1]["text"] += ch
                    else:
                        # 非標點的不匹配字元，累計計數
                        mismatch_count += 1

            chars.extend(aligned_chars)

        if not chars:
            text = "".join(r.text for r in asr_results)
            if to_traditional:
                text = _get_cc().convert(text)
            return [{"start": 0.0, "end": 0.0, "speaker": "UNKNOWN", "text": text}]

        # Step 2: 將字元切分為句子（以標點為斷點）
        sentence_end_chars = set('。！？!?')
        comma_chars = set('，,')
        max_sentence_chars = 30
        force_cut_chars = 50

        sentences_from_chars = []  # [{start, end, text, chars_list}, ...]
        buf_chars = []

        for char in chars:
            buf_chars.append(char)
            ch = char["text"]

            should_cut = False
            # 句末標點斷句
            if len(ch) == 1 and ch in sentence_end_chars:
                should_cut = True
            elif len(ch) > 1 and ch[-1] in sentence_end_chars:
                should_cut = True
            # 逗號補切（超過 max_sentence_chars）
            buf_text_len = sum(len(c["text"]) for c in buf_chars)
            if not should_cut and buf_text_len >= max_sentence_chars:
                if len(ch) == 1 and ch in comma_chars:
                    should_cut = True
                elif len(ch) > 1 and ch[-1] in comma_chars:
                    should_cut = True
            # 強制切斷
            if not should_cut and buf_text_len >= force_cut_chars:
                should_cut = True

            if should_cut and buf_chars:
                sentences_from_chars.append({
                    "start": buf_chars[0]["start"],
                    "end": buf_chars[-1]["end"],
                    "text": "".join(c["text"] for c in buf_chars),
                })
                buf_chars = []

        # 處理剩餘
        if buf_chars:
            sentences_from_chars.append({
                "start": buf_chars[0]["start"],
                "end": buf_chars[-1]["end"],
                "text": "".join(c["text"] for c in buf_chars),
            })

        if not sentences_from_chars:
            text = "".join(r.text for r in asr_results)
            if to_traditional:
                converter = _get_cc()
                text = converter.convert(text)
            return [{"start": 0.0, "end": 0.0, "speaker": "UNKNOWN", "text": text}]

        # Step 3: 為每個句子匹配語者（依時間重疊比例）
        last_speaker = diar_segments[0]["speaker"]

        for sent in sentences_from_chars:
            sent_start = sent["start"]
            sent_end = sent["end"]
            sent_duration = sent_end - sent_start

            if sent_duration <= 0:
                sent["speaker"] = last_speaker
                continue

            # 計算每個語者與此句子的時間重疊量
            speaker_overlap = {}
            for d in diar_segments:
                overlap_start = max(sent_start, d["start"])
                overlap_end = min(sent_end, d["end"])
                overlap = max(0.0, overlap_end - overlap_start)
                if overlap > 0:
                    spk = d["speaker"]
                    speaker_overlap[spk] = speaker_overlap.get(spk, 0.0) + overlap

            if speaker_overlap:
                # 選擇重疊量最大的語者
                best_speaker = max(speaker_overlap, key=speaker_overlap.get)
                sent["speaker"] = best_speaker
                last_speaker = best_speaker
            else:
                # 無重疊時使用最近的語者
                sent_mid = (sent_start + sent_end) / 2
                min_dist = float("inf")
                nearest_speaker = last_speaker
                for d in diar_segments:
                    if sent_mid < d["start"]:
                        dist = d["start"] - sent_mid
                    elif sent_mid > d["end"]:
                        dist = sent_mid - d["end"]
                    else:
                        dist = 0
                    if dist < min_dist:
                        min_dist = dist
                        nearest_speaker = d["speaker"]
                if min_dist <= 2.0:
                    sent["speaker"] = nearest_speaker
                    last_speaker = nearest_speaker
                else:
                    sent["speaker"] = last_speaker

        # Step 4: 合併相鄰同語者句子
        merged = [sentences_from_chars[0].copy()]
        for sent in sentences_from_chars[1:]:
            prev = merged[-1]
            time_gap = sent["start"] - prev["end"]
            if sent["speaker"] == prev["speaker"] and time_gap < gap_threshold:
                prev["end"] = sent["end"]
                prev["text"] += sent["text"]
            else:
                merged.append(sent.copy())

        # Step 5: 過濾雜訊
        final = []
        for seg in merged:
            duration = seg["end"] - seg["start"]
            text = seg["text"].strip()
            if duration < 0.05 and not text:
                continue
            final.append(seg)

        # Step 6: 繁體中文轉換
        if to_traditional:
            converter = _get_cc()
            for seg in final:
                seg["text"] = converter.convert(seg["text"])

        self._progress(90, f"合併完成：{len(final)} 個片段")
        return final

    # ============================================
    # 完整流程
    # ============================================

    @staticmethod
    def split_sentences(
        segments: List[Dict[str, Any]],
        max_chars: int = 30,
        force_chars: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        將合併結果分句，產生適合字幕的單句片段。

        策略：
        - 句末標點（。！？!?）為主要斷點
        - 超過 max_chars 字在逗號（，,）處補切
        - 超過 force_chars 字完全無標點則強制切斷

        Args:
            segments: 合併後的結果 [{start, end, text, ...}]
            max_chars: 逗號補切門檻
            force_chars: 強制切斷門檻

        Returns:
            [{start, end, text}, ...]
        """
        sentence_end = set('。！？!?')
        comma_chars = set('，,')
        sentences = []

        for seg in segments:
            text = seg["text"]
            if not text.strip():
                continue

            seg_start = seg["start"]
            seg_end = seg["end"]
            seg_duration = seg_end - seg_start
            total_chars = len(text)

            if total_chars == 0:
                continue

            # 以字元數比例插值估算每個字的時間
            char_duration = seg_duration / total_chars

            buf = ""
            buf_start_idx = 0  # 緩衝區起始字元索引

            for i, ch in enumerate(text):
                buf += ch

                should_cut = False

                # 規則1: 句末標點
                if ch in sentence_end:
                    should_cut = True
                # 規則2: 超過 max_chars 且遇到逗號
                elif len(buf) >= max_chars and ch in comma_chars:
                    should_cut = True
                # 規則3: 超過 force_chars 強制切斷
                elif len(buf) >= force_chars:
                    should_cut = True

                if should_cut:
                    s_start = seg_start + buf_start_idx * char_duration
                    s_end = seg_start + (i + 1) * char_duration
                    sentences.append({
                        "start": round(s_start, 3),
                        "end": round(s_end, 3),
                        "text": buf.strip(),
                    })
                    buf = ""
                    buf_start_idx = i + 1

            # 處理剩餘文字
            if buf.strip():
                s_start = seg_start + buf_start_idx * char_duration
                s_end = seg_end
                sentences.append({
                    "start": round(s_start, 3),
                    "end": round(s_end, 3),
                    "text": buf.strip(),
                })

        return sentences

    def run(
        self,
        input_path: str,
        language: str = "Chinese",
        enable_diarization: bool = True,
        to_traditional: bool = True,
    ) -> Dict[str, Any]:
        """
        完整 ASR 流程：轉檔 → 分段轉錄 → 語者分離 → 合併

        Args:
            input_path: 輸入音訊路徑（任意格式）
            language: 語言
            enable_diarization: 是否啟用語者分離
            to_traditional: 是否轉為繁體中文

        Returns:
            {
                "merged": [{start, end, speaker, text}, ...],
                "raw_text": str,
                "sentences": [{start, end, text}, ...],
            }
        """
        try:
            from audio_utils import convert_to_wav
            from config import RESULT_DIR

            # Step 1: 轉換為 WAV
            self._progress(0, "轉換音訊格式...")
            result_dir = RESULT_DIR / "work"
            result_dir.mkdir(parents=True, exist_ok=True)
            wav_path = result_dir / "converted.wav"

            # 強制重新轉換
            if wav_path.exists():
                wav_path.unlink()

            convert_to_wav(str(input_path), str(wav_path))

            # Step 2: 載入模型 + 轉錄
            self.load_model()
            asr_results = self.transcribe(str(wav_path), language=language)

            # 取得原始 ASR 全文
            raw_text = "".join(r.text for r in asr_results)
            if to_traditional:
                raw_text = _get_cc().convert(raw_text)

            # Step 3: 語者分離（可選）
            if enable_diarization:
                diar_segments = self.diarize(str(wav_path))
            else:
                diar_segments = []

            # Step 4: 合併
            segments = self.merge(
                asr_results,
                diar_segments,
                to_traditional=to_traditional,
            )

            # Step 5: 分句（適合字幕）
            sentences = self.split_sentences(segments)

            self._progress(95, "處理完成")
            return {
                "merged": segments,
                "raw_text": raw_text,
                "sentences": sentences,
            }

        finally:
            self.unload_model()
            self._progress(100, "完成")

    # ============================================
    # 匯出
    # ============================================

    @staticmethod
    def format_time(seconds: float) -> str:
        """將秒數格式化為 MM:SS.mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:06.3f}"
        return f"{m:02d}:{s:06.3f}"

    @staticmethod
    def format_srt_time(seconds: float) -> str:
        """SRT 時間格式 HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @classmethod
    def export_txt(cls, segments: List[Dict], output_path: str):
        """匯出為 TXT"""
        with open(output_path, "w", encoding="utf-8") as f:
            for seg in segments:
                start = cls.format_time(seg["start"])
                end = cls.format_time(seg["end"])
                speaker = seg.get("speaker", "")
                text = seg["text"]
                if speaker:
                    f.write(f"[{start} → {end}] {speaker}: {text}\n")
                else:
                    f.write(f"[{start} → {end}] {text}\n")

    @classmethod
    def export_srt(cls, segments: List[Dict], output_path: str):
        """匯出為 SRT 字幕"""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                start = cls.format_srt_time(seg["start"])
                end = cls.format_srt_time(seg["end"])
                speaker = seg.get("speaker", "")
                text = seg["text"]
                f.write(f"{i}\n")
                f.write(f"{start} --> {end}\n")
                if speaker:
                    f.write(f"[{speaker}] {text}\n")
                else:
                    f.write(f"{text}\n")
                f.write("\n")

    @staticmethod
    def export_raw_txt(raw_text: str, output_path: str):
        """匯出原始 ASR 全文（純文字，不含時間/語者）"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(raw_text)

    @classmethod
    def export_subtitle_txt(cls, sentences: List[Dict], output_path: str):
        """匯出 YouTube 風格字幕（時間戳+文字交錯）"""
        with open(output_path, "w", encoding="utf-8") as f:
            for sent in sentences:
                start = cls.format_time(sent["start"])
                f.write(f"{start}\n")
                f.write(f"{sent['text']}\n")

    @classmethod
    def export_subtitle_srt(cls, sentences: List[Dict], output_path: str):
        """匯出單句 SRT 字幕（適合上字幕）"""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, sent in enumerate(sentences, 1):
                start = cls.format_srt_time(sent["start"])
                end = cls.format_srt_time(sent["end"])
                f.write(f"{i}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{sent['text']}\n")
                f.write("\n")
