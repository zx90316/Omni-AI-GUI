# -*- coding: utf-8 -*-
"""
ASR 核心引擎
封裝 Omni AI 轉錄、語者分離、合併邏輯，支援 GPU/CPU 與多模型切換。

重型 ML 套件（transformers, opencc, pyannote）採用延遲匯入，
讓 GUI 能快速啟動。
"""
import bisect
import gc
import logging
import os
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional

from backend.model_registry import MODEL_IDS


logger = logging.getLogger(__name__)


# Qwen ASR 與 pyannote 都會大量占用 GPU/CPU 記憶體。背景任務可同時建立，
# 但同一個程序內只允許一條完整 ASR pipeline 執行，避免模型互搶 VRAM。
_ASR_RUN_LOCK = threading.Lock()


def get_asr_runtime_status() -> Dict[str, Any]:
    """Return process-local ASR scheduler state without loading ML dependencies."""
    from backend.asr_control import active_control_count

    return {
        "busy": _ASR_RUN_LOCK.locked(),
        "tracked_tasks": active_control_count(),
    }


class ASRCancelledError(RuntimeError):
    """Raised when a user cooperatively cancels an ASR task."""


class ASRTimeoutError(TimeoutError):
    """Raised when an ASR task exceeds its configured wall-clock deadline."""


@dataclass(frozen=True)
class ASRTimeStamp:
    """Timestamp shape consumed by the existing subtitle merge pipeline."""

    text: str
    start_time: float
    end_time: float


@dataclass
class ASRTranscription:
    """Stable adapter around the Transformers-native Qwen3-ASR result."""

    language: str
    text: str
    time_stamps: List[ASRTimeStamp]


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
        # 部分較舊的 CUDA GPU 不支援 bfloat16；強制使用會在推論階段才報錯。
        supports_bf16 = getattr(torch.cuda, "is_bf16_supported", lambda: False)()
        dtype = torch.bfloat16 if supports_bf16 else torch.float16
        return {"device": "cuda:0", "dtype": dtype, "label": "CUDA GPU"}
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
        model_name: str = MODEL_IDS["asr_1.7b"],
        device: str = "auto",
        on_progress: Optional[Callable[[float, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        timeout_seconds: Optional[float] = None,
    ):
        self.model_name = model_name
        self.on_progress = on_progress or (lambda p, m: None)
        self.should_cancel = should_cancel or (lambda: False)
        self.timeout_seconds = timeout_seconds
        self._deadline: Optional[float] = None
        self._model = None
        self._processor = None
        self._aligner_model = None
        self._aligner_processor = None
        self.max_new_tokens = max(64, int(os.getenv("ASR_MAX_NEW_TOKENS", "512")))
        self.warnings: List[str] = []

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
            supports_bf16 = getattr(torch.cuda, "is_bf16_supported", lambda: False)()
            self.dtype = torch.bfloat16 if supports_bf16 else torch.float16
            self.device_label = device

    def _interruption_reason(self) -> Optional[str]:
        """Return cancellation/timeout reason without raising (safe for callbacks)."""
        try:
            should_cancel = getattr(self, "should_cancel", None)
            if should_cancel is not None and should_cancel():
                return "cancelled"
        except Exception:
            # A broken cancellation callback must not crash model execution.
            pass
        deadline = getattr(self, "_deadline", None)
        if deadline is not None and time.monotonic() >= deadline:
            return "timeout"
        return None

    def _check_interrupted(self, stage: str) -> None:
        reason = self._interruption_reason()
        if reason == "cancelled":
            raise ASRCancelledError(f"ASR 任務已取消（階段：{stage}）")
        if reason == "timeout":
            raise ASRTimeoutError(f"ASR 任務執行逾時（階段：{stage}）")

    def _progress(self, percent: float, message: str):
        """更新進度"""
        # 進度屬於可觀測性功能，不應因 SSE 中斷或資料庫暫時鎖定而中止 ASR。
        try:
            self.on_progress(percent, message)
        except Exception as exc:
            print(f"[WARN] 更新 ASR 進度失敗（轉錄繼續）: {exc}")
        print(f"[{percent:.0f}%] {message}")

    # ============================================
    # 模型管理
    # ============================================

    def load_model(self):
        """載入 ASR 模型（含離線保護）"""
        if self._model is not None:
            return

        from transformers import (
            AutoModelForMultimodalLM,
            AutoModelForTokenClassification,
            AutoProcessor,
        )
        from backend.config import FORCED_ALIGNER
        from backend.network_utils import is_model_cached, make_offline_error_message

        for mid in [self.model_name, FORCED_ALIGNER]:
            if not is_model_cached(mid):
                raise RuntimeError(make_offline_error_message(mid))

        self._progress(5, f"載入模型 {self.model_name}...")
        try:
            model_kwargs = {
                "dtype": self.dtype,
                "device_map": self.device,
            }
            attention = os.getenv("ASR_ATTN_IMPLEMENTATION", "").strip()
            if attention:
                model_kwargs["attn_implementation"] = attention

            self._processor = AutoProcessor.from_pretrained(
                self.model_name, local_files_only=True
            )
            self._model = AutoModelForMultimodalLM.from_pretrained(
                self.model_name, local_files_only=True, **model_kwargs
            ).eval()
            self._aligner_processor = AutoProcessor.from_pretrained(
                FORCED_ALIGNER, local_files_only=True
            )
            self._aligner_model = AutoModelForTokenClassification.from_pretrained(
                FORCED_ALIGNER, local_files_only=True, **model_kwargs
            ).eval()
        except Exception as e:
            self.unload_model()
            err_str = str(e).lower()
            if any(kw in err_str for kw in ["connection", "proxy", "timeout", "resolve", "offline"]):
                raise RuntimeError(make_offline_error_message(self.model_name)) from e
            raise
        self._progress(15, "模型載入完成")

    def unload_model(self):
        """釋放模型記憶體"""
        had_model = any(
            getattr(self, name, None) is not None
            for name in ("_model", "_processor", "_aligner_model", "_aligner_processor")
        )
        self._model = None
        self._processor = None
        self._aligner_model = None
        self._aligner_processor = None
        if had_model:
            import torch
            gc.collect()
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
        with sf.SoundFile(audio_path, mode="r") as audio_file:
            sr = audio_file.samplerate
            total_duration = len(audio_file) / sr if sr else 0.0
            if total_duration <= target_duration:
                return [(0.0, total_duration)]

            self._progress(18, f"偵測靜音段... (音訊長度: {total_duration:.1f}s)")
            frame_size = int(sr * frame_duration)
            if frame_size <= 0:
                raise ValueError("靜音偵測 frame_duration 必須大於 0")

            threshold_linear = 10 ** (silence_threshold_db / 20.0)
            silence_regions = []
            in_silence = False
            silence_start = 0.0
            frame_index = 0

            # 每次只讀約 10 秒，RAM 使用量不再隨音訊長度線性成長。
            block_size = frame_size * 512
            for block in audio_file.blocks(
                blocksize=block_size,
                dtype="float32",
                always_2d=True,
            ):
                self._check_interrupted("靜音偵測")
                complete_samples = (len(block) // frame_size) * frame_size
                if complete_samples == 0:
                    continue
                frames = block[:complete_samples].reshape(-1, frame_size, block.shape[1])
                mono_frames = frames.mean(axis=2)
                rms_values = np.sqrt(np.mean(mono_frames * mono_frames, axis=1))

                for rms in rms_values:
                    time_sec = frame_index * frame_duration
                    if rms < threshold_linear:
                        if not in_silence:
                            in_silence = True
                            silence_start = time_sec
                    elif in_silence:
                        silence_end = time_sec
                        if silence_end - silence_start >= min_silence_duration:
                            silence_regions.append((silence_start, silence_end))
                        in_silence = False
                    frame_index += 1

            if in_silence:
                silence_end = min(total_duration, frame_index * frame_duration)
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

    @staticmethod
    def _approximate_timestamps(text: str, audio_path: str) -> List[ASRTimeStamp]:
        """Preserve usable subtitles when the optional forced alignment stage fails."""
        import soundfile as sf

        tokens: List[str] = []
        buffer: List[str] = []

        def flush_buffer():
            if buffer:
                tokens.append("".join(buffer))
                buffer.clear()

        for ch in text:
            codepoint = ord(ch)
            is_cjk = (
                0x3400 <= codepoint <= 0x9FFF
                or 0x3040 <= codepoint <= 0x30FF
            )
            if ch.isspace() or _is_punctuation(ch):
                flush_buffer()
            elif is_cjk:
                flush_buffer()
                tokens.append(ch)
            else:
                buffer.append(ch)
        flush_buffer()

        if not tokens:
            return []
        duration = max(0.0, float(sf.info(audio_path).duration))
        step = duration / len(tokens) if duration else 0.0
        return [
            ASRTimeStamp(
                text=token,
                start_time=index * step,
                end_time=(index + 1) * step,
            )
            for index, token in enumerate(tokens)
        ]

    def _transcribe_single(self, audio_path: str, language: Optional[str] = "Chinese"):
        """Use the official Transformers-native ASR and forced-aligner flow."""
        import numpy as np
        import soundfile as sf
        import torch

        self._check_interrupted("ASR 輸入準備")

        # Passing a Windows file path makes Transformers load TorchCodec, whose
        # native DLL frequently fails when its FFmpeg/PyTorch ABI differs. Our
        # pipeline already guarantees 16 kHz PCM WAV, so decode it once with
        # soundfile and reuse the waveform for ASR and forced alignment.
        waveform, sample_rate = sf.read(
            audio_path,
            dtype="float32",
            always_2d=True,
        )
        if sample_rate != 16000:
            raise ValueError(f"ASR 音訊取樣率必須為 16000 Hz，目前為 {sample_rate} Hz")
        if waveform.size == 0:
            raise ValueError("ASR 音訊沒有可辨識的樣本")
        waveform = np.ascontiguousarray(waveform.mean(axis=1), dtype=np.float32)

        inputs = self._processor.apply_transcription_request(
            audio=waveform,
            language=language or None,
        ).to(self._model.device, self._model.dtype)
        generation_kwargs = {
            "max_new_tokens": self.max_new_tokens,
            "do_sample": False,
        }
        if (
            getattr(self, "timeout_seconds", None) is not None
            or getattr(self, "should_cancel", None) is not None
        ):
            from transformers import StoppingCriteria, StoppingCriteriaList

            engine = self

            class InterruptionCriteria(StoppingCriteria):
                def __call__(self, input_ids, scores, **kwargs):
                    return torch.full(
                        (input_ids.shape[0],),
                        engine._interruption_reason() is not None,
                        device=input_ids.device,
                        dtype=torch.bool,
                    )

            generation_kwargs["stopping_criteria"] = StoppingCriteriaList(
                [InterruptionCriteria()]
            )

        with torch.inference_mode():
            output_ids = self._model.generate(**inputs, **generation_kwargs)
        self._check_interrupted("ASR 模型生成")
        generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
        parsed = self._processor.decode(generated_ids, return_format="parsed")[0]
        text = str(parsed.get("transcription") or "")
        detected_language = str(parsed.get("language") or language or "")

        timestamps: List[ASRTimeStamp] = []
        if text:
            try:
                self._check_interrupted("時間對齊輸入準備")
                aligner_inputs, word_lists = self._aligner_processor.prepare_forced_aligner_inputs(
                    audio=waveform,
                    transcript=text,
                    language=detected_language,
                )
                aligner_inputs = aligner_inputs.to(
                    self._aligner_model.device,
                    self._aligner_model.dtype,
                )
                with torch.inference_mode():
                    aligned = self._aligner_model(**aligner_inputs)
                self._check_interrupted("時間對齊")
                decoded = self._aligner_processor.decode_forced_alignment(
                    logits=aligned.logits,
                    input_ids=aligner_inputs["input_ids"],
                    word_lists=word_lists,
                    timestamp_token_id=self._aligner_model.config.timestamp_token_id,
                    timestamp_segment_time=getattr(
                        self._aligner_model.config, "timestamp_segment_time", None
                    ),
                )[0]
                timestamps = [
                    ASRTimeStamp(
                        text=str(item.get("text", "")),
                        start_time=float(item.get("start_time", 0.0)),
                        end_time=float(item.get("end_time", 0.0)),
                    )
                    for item in decoded
                ]
            except (ASRCancelledError, ASRTimeoutError):
                raise
            except Exception:
                logger.warning(
                    "Forced alignment failed; using approximate timestamps for %s",
                    audio_path,
                    exc_info=True,
                )
                self._progress(55, "精確時間對齊失敗，改用近似時間戳...")
                self.warnings.append("精確時間對齊失敗，時間戳為近似值")
                timestamps = self._approximate_timestamps(text, audio_path)

        return [
            ASRTranscription(
                language=detected_language,
                text=text,
                time_stamps=timestamps,
            )
        ]

    def _transcribe_with_retry(
        self,
        audio_path: str,
        language: Optional[str],
        max_attempts: int = 2,
    ):
        """轉錄單段音訊，對暫時性推論失敗做一次有界重試。"""
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                self._check_interrupted("片段轉錄")
                return self._transcribe_single(audio_path, language)
            except (ASRCancelledError, ASRTimeoutError):
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= max_attempts:
                    break

                self._progress(20, f"片段轉錄失敗，正在重試 ({attempt}/{max_attempts - 1})...")
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
                time.sleep(0.5)

        raise RuntimeError(
            f"音訊片段轉錄在 {max_attempts} 次嘗試後仍失敗: {last_error}"
        ) from last_error

    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = "Chinese",
        target_duration: float = 60.0,
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
        from backend.audio_utils import get_audio_duration

        total_duration = get_audio_duration(str(audio_path))
        if total_duration <= 0:
            raise ValueError("音訊內容為空或無法取得有效長度")

        # 短音訊直接整段處理
        if total_duration <= target_duration:
            self._progress(25, "短音訊，直接整段轉錄...")
            results = self._transcribe_with_retry(str(audio_path), language)
            self._progress(60, f"轉錄完成，共 {len(results)} 段")
            return results

        # 長音訊分段處理
        chunks = self.split_audio_by_silence(str(audio_path), target_duration=target_duration)
        all_results = []
        result_dir = Path(audio_path).parent
        # 逐段從檔案讀取，避免長音訊再次整段載入記憶體。
        with sf.SoundFile(str(audio_path), mode="r") as audio_file:
            sr = audio_file.samplerate

            for i, (chunk_start, chunk_end) in enumerate(chunks):
                self._check_interrupted(f"片段 {i + 1}/{len(chunks)}")
                progress = 20 + (i / len(chunks)) * 40
                self._progress(progress, f"轉錄片段 {i+1}/{len(chunks)}: {chunk_start:.1f}s → {chunk_end:.1f}s")

                chunk_path = result_dir / f"chunk_{i:04d}.wav"
                start_sample = max(0, int(chunk_start * sr))
                end_sample = min(len(audio_file), int(chunk_end * sr))
                frame_count = end_sample - start_sample
                if frame_count <= 0:
                    raise ValueError(f"音訊片段 {i + 1} 長度無效")

                audio_file.seek(start_sample)
                chunk_data = audio_file.read(frame_count, dtype="float32", always_2d=True)
                sf.write(str(chunk_path), chunk_data, sr)

                try:
                    chunk_results = self._transcribe_with_retry(str(chunk_path), language)

                    # 計算精確的時間戳偏移（避免 float 捨去誤差）
                    exact_chunk_start = start_sample / sr

                    # 修正時間戳偏移
                    for r in chunk_results:
                        if hasattr(r, 'time_stamps') and r.time_stamps:
                            new_timestamps = []
                            for ts in r.time_stamps:
                                kwargs = {}
                                if hasattr(ts, 'start_time') and ts.start_time is not None:
                                    kwargs['start_time'] = ts.start_time + exact_chunk_start
                                if hasattr(ts, 'end_time') and ts.end_time is not None:
                                    kwargs['end_time'] = ts.end_time + exact_chunk_start
                                if kwargs:
                                    new_timestamps.append(dc_replace(ts, **kwargs))
                                else:
                                    new_timestamps.append(ts)
                            r.time_stamps = new_timestamps

                    all_results.extend(chunk_results)
                finally:
                    # 單段失敗時也清理，避免下次任務讀到舊片段。
                    chunk_path.unlink(missing_ok=True)

        self._progress(60, f"轉錄完成，共 {len(all_results)} 段")
        return all_results

    # ============================================
    # 語者分離
    # ============================================

    def diarize(self, audio_path: str) -> List[Dict]:
        """執行語者分離（含離線保護）"""
        self._check_interrupted("語者分離模型載入")
        self._progress(62, "載入語者分離模型...")

        import torch
        from pyannote.audio import Pipeline
        from backend.audio_utils import load_audio
        from backend.config import DIARIZATION_MODEL, HF_TOKEN
        from backend.network_utils import is_model_cached, make_offline_error_message

        if not is_model_cached(DIARIZATION_MODEL):
            raise RuntimeError(make_offline_error_message(DIARIZATION_MODEL))

        pipeline = None
        try:
            pipeline = Pipeline.from_pretrained(
                DIARIZATION_MODEL,
                token=HF_TOKEN or None,
            )
        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in ["connection", "proxy", "timeout", "resolve", "offline"]):
                raise RuntimeError(make_offline_error_message(DIARIZATION_MODEL)) from e
            raise

        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            pipeline.to(torch.device(device))

            self._progress(68, "執行語者分離...")
            waveform, sample_rate = load_audio(str(audio_path))
            self._check_interrupted("語者分離推論")
            output = pipeline({"waveform": waveform, "sample_rate": sample_rate})
            self._check_interrupted("語者分離結果處理")

            diarization_result = output.speaker_diarization
            diar_segments = []
            for turn, _, speaker in diarization_result.itertracks(yield_label=True):
                diar_segments.append({
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker,
                })
        finally:
            # 推論失敗也必須釋放 pipeline，否則後續任務容易因 VRAM 殘留失敗。
            if pipeline is not None and torch.cuda.is_available():
                try:
                    pipeline.to(torch.device("cpu"))
                except Exception:
                    pass
            pipeline = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

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
        mode: str = "subtitle",
        max_sentence_chars: int = 30,
        force_cut_chars: int = 50,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
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
            dummy_chars = [{"start": 0.0, "end": 0.0, "speaker": "UNKNOWN", "text": text}]
            return dummy_chars, dummy_chars

        final = self.build_sentences_from_chars(
            chars=chars,
            diar_segments=diar_segments,
            mode=mode,
            max_sentence_chars=max_sentence_chars,
            force_cut_chars=force_cut_chars,
            to_traditional=to_traditional,
        )
        return final, chars

    def build_sentences_from_chars(
        self,
        chars: List[Dict[str, Any]],
        diar_segments: List[Dict],
        mode: str = "subtitle",
        max_sentence_chars: int = 30,
        force_cut_chars: int = 50,
        to_traditional: bool = True,
    ) -> List[Dict[str, Any]]:
        """從字元級對齊結果重新分句與匹配語者"""
        if not chars:
            text = ""
            if to_traditional:
                text = _get_cc().convert(text)
            return [{"start": 0.0, "end": 0.0, "speaker": "UNKNOWN", "text": text}]

        # Step 2: 處理分段
        sentences_from_chars = []  # [{start, end, text, chars_list}, ...]
        buf_chars = []

        if mode == "subtitle":
            sentence_end_chars = set('。！？!?')
            comma_chars = set('，,')
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
                    valid_start = buf_chars[0]["start"]
                    for c in buf_chars:
                        if c["text"].strip() and c["end"] > c["start"]:
                            valid_start = c["start"]
                            break
                    sentences_from_chars.append({
                        "start": valid_start,
                        "end": buf_chars[-1]["end"],
                        "text": "".join(c["text"] for c in buf_chars),
                    })
                    buf_chars = []
        else:
            # Diarization mode: split cautiously without char counts limits mostly
            sentence_end_chars = set('。！？!?')
            for i, char in enumerate(chars):
                buf_chars.append(char)
                ch = char["text"]

                should_cut = False
                if len(ch) == 1 and ch in sentence_end_chars:
                    should_cut = True
                elif len(ch) > 1 and ch[-1] in sentence_end_chars:
                    should_cut = True
                
                # Check for large time gaps (e.g. > 1.5s) to split paragraphs
                if i + 1 < len(chars):
                    next_char = chars[i+1]
                    if next_char["start"] - char["end"] > 1.5:
                        should_cut = True
                
                # Only force cut if it's crazy long (> 150 chars)
                buf_text_len = sum(len(c["text"]) for c in buf_chars)
                if not should_cut and buf_text_len >= 150:
                    should_cut = True

                if should_cut and buf_chars:
                    valid_start = buf_chars[0]["start"]
                    for c in buf_chars:
                        if c["text"].strip() and c["end"] > c["start"]:
                            valid_start = c["start"]
                            break
                    sentences_from_chars.append({
                        "start": valid_start,
                        "end": buf_chars[-1]["end"],
                        "text": "".join(c["text"] for c in buf_chars),
                    })
                    buf_chars = []

        # 處理剩餘
        if buf_chars:
            valid_start = buf_chars[0]["start"]
            for c in buf_chars:
                if c["text"].strip() and c["end"] > c["start"]:
                    valid_start = c["start"]
                    break
                    
            sentences_from_chars.append({
                "start": valid_start,
                "end": buf_chars[-1]["end"],
                "text": "".join(c["text"] for c in buf_chars),
            })

        if not sentences_from_chars:
            text = "".join(c["text"] for c in chars)
            if to_traditional:
                converter = _get_cc()
                text = converter.convert(text)
            return [{"start": 0.0, "end": 0.0, "speaker": "UNKNOWN", "text": text}]

        # Step 3: 為每個句子匹配語者（依時間重疊比例）
        last_speaker = diar_segments[0]["speaker"] if diar_segments else "UNKNOWN"

        for sent in sentences_from_chars:
            sent_start = sent["start"]
            sent_end = sent["end"]
            sent_duration = sent_end - sent_start

            if sent_duration <= 0 or not diar_segments:
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

        # Step 4: 合併同語者的連續句子 (針對 diarization 模式特別重要)
        merged = [sentences_from_chars[0].copy()]
        for sent in sentences_from_chars[1:]:
            prev = merged[-1]
            time_gap = sent["start"] - prev["end"]
            
            should_merge = False
            if sent["speaker"] == prev["speaker"]:
                if mode == "diarization":
                    # 在 diarization 模式下，只要是同個語者且中間沒有過長的靜音，就合併成同一句
                    if time_gap < 1.5:
                        should_merge = True
                else:
                    # 在 subtitle 模式下，只有極短的碎片才合併，避免字幕過長
                    if time_gap < 0.2 and len(sent["text"]) < 5 and len(prev["text"]) < 40:
                        should_merge = True

            if should_merge:
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
            for c in chars:
                c["text"] = converter.convert(c["text"])

        self._progress(90, f"合併完成：{len(final)} 個片段")
        return final

    # ============================================
    # 語者歸組
    # ============================================

    @staticmethod
    def group_by_speaker(
        sentences: List[Dict[str, Any]],
        gap_threshold: float = 1.5,
    ) -> List[Dict[str, Any]]:
        """
        將語者分離逐句結果歸組為段落結構。
        同語者 + 間隔 < gap_threshold → 合併為同一段落。

        輸入: [{start, end, speaker, text}, ...]
        輸出: [{speaker, start, end, segments: [{start, end, text}], combined_text}, ...]
        """
        if not sentences:
            return []

        groups = []
        current_group = {
            "speaker": sentences[0].get("speaker", "UNKNOWN"),
            "start": sentences[0]["start"],
            "end": sentences[0]["end"],
            "segments": [{
                "start": sentences[0]["start"],
                "end": sentences[0]["end"],
                "text": sentences[0]["text"],
            }],
        }

        for sent in sentences[1:]:
            spk = sent.get("speaker", "UNKNOWN")
            time_gap = sent["start"] - current_group["end"]

            if spk == current_group["speaker"] and time_gap < gap_threshold:
                # 同語者且間隔短 → 併入當前段落
                current_group["end"] = sent["end"]
                current_group["segments"].append({
                    "start": sent["start"],
                    "end": sent["end"],
                    "text": sent["text"],
                })
            else:
                # 語者切換或間隔過長 → 結束舊段落，開新段落
                current_group["combined_text"] = "".join(
                    seg["text"] for seg in current_group["segments"]
                )
                groups.append(current_group)
                current_group = {
                    "speaker": spk,
                    "start": sent["start"],
                    "end": sent["end"],
                    "segments": [{
                        "start": sent["start"],
                        "end": sent["end"],
                        "text": sent["text"],
                    }],
                }

        # 處理最後一個段落
        current_group["combined_text"] = "".join(
            seg["text"] for seg in current_group["segments"]
        )
        groups.append(current_group)

        return groups

    # ============================================
    # 完整流程
    # ============================================

    def run(
        self,
        input_path: str,
        language: Optional[str] = "Chinese",
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
        timeout_seconds = getattr(self, "timeout_seconds", None)
        self.warnings = []
        self._deadline = (
            time.monotonic() + float(timeout_seconds)
            if timeout_seconds is not None
            else None
        )
        self._check_interrupted("任務啟動")

        lock_acquired = _ASR_RUN_LOCK.acquire(blocking=False)
        if not lock_acquired:
            self._progress(0, "等待其他 ASR 任務釋放運算資源...")
            while not lock_acquired:
                self._check_interrupted("等待運算資源")
                lock_acquired = _ASR_RUN_LOCK.acquire(timeout=0.25)

        try:
            from backend.audio_utils import convert_to_wav
            from backend.config import RESULT_DIR

            # Step 1: 轉換為 WAV
            self._progress(0, "轉換音訊格式...")
            work_root = Path(RESULT_DIR) / "work"
            work_root.mkdir(parents=True, exist_ok=True)

            # 每個任務使用獨立工作目錄，杜絕 converted.wav / chunk_*.wav 競態。
            with tempfile.TemporaryDirectory(
                prefix="asr_",
                dir=work_root,
                ignore_cleanup_errors=True,
            ) as temp_dir:
                wav_path = Path(temp_dir) / "converted.wav"
                try:
                    convert_to_wav(
                        str(input_path),
                        str(wav_path),
                        should_cancel=lambda: self._interruption_reason() is not None,
                    )
                except InterruptedError as exc:
                    self._check_interrupted("音訊轉換")
                    raise ASRCancelledError("ASR 任務在音訊轉換階段中止") from exc

                # Step 2: 載入模型 + 轉錄
                self._check_interrupted("模型載入")
                self.load_model()
                asr_results = self.transcribe(str(wav_path), language=language)

                # 轉錄完成後立即卸載 ASR 模型，騰出 VRAM
                self.unload_model()

                # 取得原始 ASR 全文
                raw_text = "".join((getattr(r, "text", "") or "") for r in asr_results)
                if to_traditional:
                    raw_text = _get_cc().convert(raw_text)

                # Step 3: 語者分離（可選）
                if enable_diarization:
                    self._check_interrupted("語者分離")
                    diar_segments = self.diarize(str(wav_path))
                else:
                    diar_segments = []

                # Step 4: 產出字幕短句（subtitle mode）
                subtitle_sentences, chars = self.merge(
                    asr_results,
                    diar_segments,
                    to_traditional=to_traditional,
                    mode="subtitle",
                )

                # Step 5: 產出語者歸組段落（diarization mode，不重跑 ASR）
                diarization_result = None
                if enable_diarization and diar_segments:
                    diar_sentences = self.build_sentences_from_chars(
                        chars=chars,
                        diar_segments=diar_segments,
                        mode="diarization",
                        to_traditional=False,  # chars 已經在 Step4 轉過繁體
                    )
                    diarization_result = self.group_by_speaker(diar_sentences)

                self._progress(95, "處理完成")
                self._check_interrupted("結果完成")
                return {
                    "raw_text": raw_text,
                    "sentences": subtitle_sentences,
                    "diarization_result": diarization_result,
                    "chars": chars,
                    "diar_segments": diar_segments,
                    "warnings": list(dict.fromkeys(self.warnings)),
                }

        finally:
            # 確保模型與全域運算鎖在任何例外下都被釋放。
            try:
                self.unload_model()
            finally:
                if lock_acquired:
                    _ASR_RUN_LOCK.release()

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
