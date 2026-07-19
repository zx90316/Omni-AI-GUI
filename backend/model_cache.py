# -*- coding: utf-8 -*-
"""Pure-filesystem inspection of the Hugging Face Hub cache."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
from typing import Mapping


@dataclass(frozen=True)
class ModelCacheStatus:
    model_id: str
    state: str
    cached: bool
    detail: str
    revision: str | None = None
    snapshot_path: str | None = None
    size_bytes: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def get_hf_cache_dir(
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve the Hub cache using Hugging Face environment precedence."""
    env = os.environ if environ is None else environ
    explicit = env.get("HF_HUB_CACHE") or env.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit).expanduser()
    hf_home = env.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "hub"
    resolved_home = Path.home() if home is None else home
    return resolved_home / ".cache" / "huggingface" / "hub"


def _repo_cache_dir(model_id: str, cache_dir: Path) -> Path:
    return cache_dir / f"models--{model_id.replace('/', '--')}"


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _snapshot_is_usable(snapshot: Path) -> bool:
    """A snapshot must contain at least one readable, non-broken file."""
    found_file = False
    try:
        for item in snapshot.rglob("*"):
            if item.is_symlink() and not item.exists():
                return False
            if item.is_file():
                found_file = True
    except OSError:
        return False
    if not found_file:
        return False

    # Some Hugging Face pipelines reference component directories from YAML.
    # A Windows symlink failure can leave the YAML and one component present,
    # which previously looked "ready" even though inference could not start.
    config_yaml = snapshot / "config.yaml"
    if config_yaml.is_file():
        try:
            referenced = set(
                re.findall(
                    r"\$model/([A-Za-z0-9_.-]+)",
                    config_yaml.read_text(encoding="utf-8"),
                )
            )
            weight_suffixes = (
                ".safetensors",
                ".bin",
                ".pt",
                ".pth",
                ".onnx",
                ".npz",
                ".pdparams",
            )
            for component in referenced:
                component_path = snapshot / component
                if not component_path.is_dir() or not any(
                    item.is_file()
                    and item.stat().st_size > 0
                    and item.name.lower().endswith(weight_suffixes)
                    for item in component_path.rglob("*")
                ):
                    return False
        except (OSError, UnicodeError):
            return False
    return True


def _snapshot_has_complete_weights(snapshot: Path) -> bool:
    """Verify weight files referenced by an index, or one standalone weight file."""
    try:
        indexes = list(snapshot.rglob("*.index.json"))
        if indexes:
            for index_path in indexes:
                try:
                    payload = json.loads(index_path.read_text(encoding="utf-8"))
                    filenames = set(payload.get("weight_map", {}).values())
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                    return False
                if not filenames or not all(
                    (index_path.parent / filename).is_file()
                    and (index_path.parent / filename).stat().st_size > 0
                    for filename in filenames
                ):
                    return False
            return True

        return any(
            item.is_file()
            and item.stat().st_size > 0
            and item.name.lower().endswith(
                (".safetensors", ".bin", ".pt", ".pth", ".onnx", ".pdparams")
            )
            for item in snapshot.rglob("*")
        )
    except OSError:
        return False


def inspect_model_cache(model_id: str, cache_dir: Path | None = None) -> ModelCacheStatus:
    """Classify a model cache as ``ready``, ``partial`` or ``missing``."""
    root = cache_dir or get_hf_cache_dir()
    repo_dir = _repo_cache_dir(model_id, root)
    if not repo_dir.is_dir():
        return ModelCacheStatus(model_id, "missing", False, "尚未下載")

    snapshots_dir = repo_dir / "snapshots"
    try:
        snapshots = [path for path in snapshots_dir.iterdir() if path.is_dir()]
    except OSError:
        snapshots = []

    usable = {path.name: path for path in snapshots if _snapshot_is_usable(path)}
    incomplete = []
    try:
        incomplete = list(repo_dir.rglob("*.incomplete"))
    except OSError:
        pass

    revision = None
    ref_revision = None
    refs_dir = repo_dir / "refs"
    main_ref = refs_dir / "main"
    if main_ref.is_file():
        try:
            revision = main_ref.read_text(encoding="utf-8").strip() or None
            ref_revision = revision
        except OSError:
            revision = None

    selected = usable.get(revision) if revision else None
    if selected is None and usable:
        selected = max(usable.values(), key=lambda path: path.stat().st_mtime)
        revision = selected.name

    size_bytes = _directory_size(repo_dir)
    if selected is None:
        detail = "快取不完整：找不到可用 snapshot" if snapshots else "下載尚未完成"
        return ModelCacheStatus(model_id, "partial", False, detail, size_bytes=size_bytes)

    if not _snapshot_has_complete_weights(selected):
        return ModelCacheStatus(
            model_id,
            "partial",
            False,
            (
                f"偵測到 {len(incomplete)} 個未完成下載檔，且有效 snapshot 缺少完整權重"
                if incomplete
                else "有效 snapshot 缺少模型權重"
            ),
            revision,
            str(selected),
            size_bytes,
        )

    if ref_revision and ref_revision not in usable:
        return ModelCacheStatus(
            model_id,
            "partial",
            False,
            "main revision 未指向有效 snapshot",
            ref_revision,
            str(selected),
            size_bytes,
        )

    return ModelCacheStatus(
        model_id,
        "ready",
        True,
        (
            f"已下載且 snapshot 可讀（忽略 {len(incomplete)} 個未引用暫存檔）"
            if incomplete
            else "已下載且 snapshot 可讀"
        ),
        revision,
        str(selected),
        size_bytes,
    )
