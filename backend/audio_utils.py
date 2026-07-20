# -*- coding: utf-8 -*-
"""
音訊處理工具模組
處理音訊格式轉換、載入等功能
"""
from fractions import Fraction
from pathlib import Path
from typing import Callable, Tuple, Optional

import av
import soundfile as sf
import torch
import torchaudio

from backend.config import AUDIO_SAMPLE_RATE


# ============================================
# torchaudio 補丁 (soundfile 後端)
# ============================================

class MockAudioMetaData:
    """偽造 AudioMetaData 物件 (讓 Pyannote 正常運作)"""
    def __init__(self, sample_rate: int, num_frames: int, num_channels: int):
        self.sample_rate = sample_rate
        self.num_frames = num_frames
        self.num_channels = num_channels
        self.bits_per_sample = 16
        self.encoding = "PCM_S"


def patched_info(filepath, **kwargs) -> MockAudioMetaData:
    """手動實作 torchaudio.info (改用 soundfile)"""
    sinfo = sf.info(filepath)
    return MockAudioMetaData(sinfo.samplerate, sinfo.frames, sinfo.channels)


def patched_load(filepath, **kwargs) -> Tuple[torch.Tensor, int]:
    """手動實作 torchaudio.load (改用 soundfile)"""
    data, samplerate = sf.read(filepath, dtype='float32', always_2d=True)
    data = data.T
    tensor = torch.from_numpy(data)
    return tensor, samplerate


def apply_torchaudio_patches():
    """套用 torchaudio 補丁"""
    torchaudio.info = patched_info
    torchaudio.load = patched_load
    torchaudio.list_audio_backends = lambda: ["soundfile"]
    torchaudio.get_audio_backend = lambda: "soundfile"
    torchaudio.AudioMetaData = MockAudioMetaData
    print("[OK] torchaudio 補丁已套用（使用 soundfile 後端）")


# ============================================
# 音訊處理函式
# ============================================

def convert_to_wav(
    input_path: str,
    output_path: str,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> str:
    """
    轉換音訊為 WAV 格式 (16kHz 單聲道)

    Args:
        input_path: 輸入檔案路徑
        output_path: 輸出 WAV 檔案路徑
        start_time: 開始時間 (秒)
        end_time: 結束時間 (秒)

    Returns:
        輸出檔案路徑
    """
    output_path = Path(output_path)
    if output_path.exists():
        return str(output_path)

    with av.open(input_path) as input_container:
        if not input_container.streams.audio:
            raise ValueError(f"檔案不包含可辨識的音軌: {input_path}")
        input_stream = input_container.streams.audio[0]

        resampler = av.audio.resampler.AudioResampler(
            format='s16',
            layout='mono',
            rate=AUDIO_SAMPLE_RATE
        )

        if start_time is not None:
            input_container.seek(int(start_time * av.time_base))

        with av.open(str(output_path), 'w', format='wav') as output_container:
            output_stream = output_container.add_stream(
                'pcm_s16le',
                rate=AUDIO_SAMPLE_RATE,
                layout='mono'
            )

            selection_start = max(0.0, start_time or 0.0)
            selected_sample_count = (
                max(0, round((end_time - selection_start) * AUDIO_SAMPLE_RATE))
                if end_time is not None
                else None
            )
            written_samples = 0
            current_time = selection_start

            def encode_resampled_frame(resampled_frame, time_hint: float) -> bool:
                """Encode one frame and report when the selected range is complete."""
                nonlocal written_samples

                # Keep the original zero-copy path for full-file conversion.
                if start_time is None and end_time is None:
                    for packet in output_stream.encode(resampled_frame):
                        output_container.mux(packet)
                    return False

                frame_start = (
                    float(resampled_frame.pts * resampled_frame.time_base)
                    if resampled_frame.pts is not None
                    else time_hint
                )
                frame_end = frame_start + (resampled_frame.samples / AUDIO_SAMPLE_RATE)
                if frame_end <= selection_start:
                    return False

                start_offset = max(
                    0,
                    min(
                        resampled_frame.samples,
                        round((selection_start - frame_start) * AUDIO_SAMPLE_RATE),
                    ),
                )
                available_samples = resampled_frame.samples - start_offset
                if selected_sample_count is not None:
                    remaining_samples = selected_sample_count - written_samples
                    if remaining_samples <= 0:
                        return True
                    available_samples = min(available_samples, remaining_samples)

                if available_samples <= 0:
                    return selected_sample_count is not None and written_samples >= selected_sample_count

                samples = resampled_frame.to_ndarray()
                clipped = av.AudioFrame.from_ndarray(
                    samples[:, start_offset:start_offset + available_samples].copy(),
                    format='s16',
                    layout='mono',
                )
                clipped.sample_rate = AUDIO_SAMPLE_RATE
                clipped.pts = written_samples
                clipped.time_base = Fraction(1, AUDIO_SAMPLE_RATE)
                for packet in output_stream.encode(clipped):
                    output_container.mux(packet)
                written_samples += available_samples
                return selected_sample_count is not None and written_samples >= selected_sample_count

            selection_complete = False

            for frame in input_container.decode(input_stream):
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("音訊轉換已取消或逾時")
                frame_time = float(frame.pts * frame.time_base) if frame.pts is not None else current_time

                if end_time is not None and frame_time >= end_time:
                    break

                resampled_frames = resampler.resample(frame)

                for resampled_frame in resampled_frames:
                    if encode_resampled_frame(resampled_frame, frame_time):
                        selection_complete = True
                        break

                current_time = frame_time
                if selection_complete:
                    break

            # 將 resampler 內部尚未輸出的尾端樣本送入 encoder，避免音訊被截短。
            if not selection_complete:
                for resampled_frame in resampler.resample(None):
                    if encode_resampled_frame(resampled_frame, current_time):
                        break

            for packet in output_stream.encode():
                output_container.mux(packet)

    print(f"[OK] 音訊已轉換: {output_path}")
    return str(output_path)


def load_audio(filepath: str) -> Tuple[torch.Tensor, int]:
    """載入音訊檔案"""
    return torchaudio.load(filepath)


def get_audio_duration(filepath: str) -> float:
    """取得音訊時長 (秒)"""
    info = sf.info(filepath)
    return info.duration


# 啟動時自動套用補丁
apply_torchaudio_patches()
