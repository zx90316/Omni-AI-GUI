# -*- coding: utf-8 -*-
"""Single source of truth for locally cached AI models."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    model_id: str
    feature: str
    source: str = "huggingface"
    gated: bool = False
    optional: bool = False


MODEL_SPECS = (
    ModelSpec("asr_1.7b", "Qwen3 ASR 1.7B", "Qwen/Qwen3-ASR-1.7B-hf", "語音辨識"),
    ModelSpec("asr_0.6b", "Qwen3 ASR 0.6B", "Qwen/Qwen3-ASR-0.6B-hf", "語音辨識"),
    ModelSpec(
        "forced_aligner",
        "Qwen3 Forced Aligner",
        "Qwen/Qwen3-ForcedAligner-0.6B-hf",
        "時間對齊",
    ),
    ModelSpec(
        "diarization",
        "Pyannote Speaker Diarization",
        "pyannote/speaker-diarization-community-1",
        "語者分離",
        gated=True,
        optional=True,
    ),
    ModelSpec("clip", "CLIP ViT-L/14", "openai/clip-vit-large-patch14", "以圖搜頁"),
    ModelSpec("bge_reranker", "BGE Reranker v2 M3", "BAAI/bge-reranker-v2-m3", "語意排序"),
    ModelSpec("bge_embedding", "BGE M3", "BAAI/bge-m3", "語意向量"),
    ModelSpec("glm_ocr", "GLM-OCR", "zai-org/GLM-OCR", "文件辨識"),
    ModelSpec(
        "pp_doclayout",
        "PP-DocLayoutV3",
        "PaddlePaddle/PP-DocLayoutV3_safetensors",
        "OCR 版面分析",
        optional=True,
    ),
)

MODEL_SPECS_BY_KEY = {spec.key: spec for spec in MODEL_SPECS}
MODEL_IDS = {spec.key: spec.model_id for spec in MODEL_SPECS}
REQUIRED_MODELS = {spec.key: spec.model_id for spec in MODEL_SPECS}


def get_model_spec(key: str) -> ModelSpec:
    """Return a registered model or raise a clear error for an unknown key."""
    try:
        return MODEL_SPECS_BY_KEY[key]
    except KeyError as exc:
        raise ValueError(f"未知的模型代碼: {key}") from exc
