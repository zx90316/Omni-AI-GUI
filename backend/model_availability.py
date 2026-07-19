# -*- coding: utf-8 -*-
"""Feature-level guards for Manager-owned model installation.

Inference endpoints must never be the place where a model is downloaded. The
Manager owns installation; API routes use this module to fail before accepting
large uploads or creating background tasks.
"""

from collections.abc import Iterable

from backend.model_cache import inspect_model_cache
from backend.model_registry import MODEL_SPECS_BY_KEY, ModelSpec


def model_key_for_id(model_id: str) -> str:
    """Resolve a registered model id to its public registry key."""
    for key, spec in MODEL_SPECS_BY_KEY.items():
        if spec.model_id == model_id:
            return key
    raise ValueError(f"Unregistered model id: {model_id}")


def missing_model_specs(model_keys: Iterable[str]) -> list[ModelSpec]:
    """Return unique registered models whose local snapshots are not usable."""
    missing: list[ModelSpec] = []
    seen: set[str] = set()
    for key in model_keys:
        if key in seen:
            continue
        seen.add(key)
        try:
            spec = MODEL_SPECS_BY_KEY[key]
        except KeyError as exc:
            raise ValueError(f"Unregistered model key: {key}") from exc
        if not inspect_model_cache(spec.model_id).cached:
            missing.append(spec)
    return missing


def require_models(model_keys: Iterable[str]) -> None:
    """Reject a feature request until every required model is Manager-ready."""
    from fastapi import HTTPException

    missing = missing_model_specs(model_keys)
    if not missing:
        return
    labels = "、".join(spec.label for spec in missing)
    raise HTTPException(
        status_code=409,
        detail=f"必要模型尚未下載或不完整：{labels}。請先在 Manager 的「模型管理」完成下載與驗證。",
    )


def require_asr_models(model_id: str, *, diarization: bool = False) -> None:
    keys = [model_key_for_id(model_id), "forced_aligner"]
    if diarization:
        keys.append("diarization")
    require_models(keys)
