# -*- coding: utf-8 -*-
"""
GPU / CUDA 自動偵測模組

偵測 NVIDIA GPU 並決定適合的 PyTorch 安裝版本。
透過執行 nvidia-smi 命令來取得 GPU 資訊與驅動支援的 CUDA 版本，
再映射到 PyTorch 官方提供的 wheel index URL。
"""
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

# PyTorch 支援的 compute platform 與對應的 index URL
# 按 CUDA 版本由高到低排列，優先匹配最新版本
PYTORCH_PLATFORMS = [
    {"min_cuda": "12.8", "platform": "cu128"},
    {"min_cuda": "12.6", "platform": "cu126"},
    {"min_cuda": "12.4", "platform": "cu126"},
    {"min_cuda": "12.2", "platform": "cu126"},
    {"min_cuda": "12.1", "platform": "cu126"}
]

PYTORCH_INDEX_BASE = "https://download.pytorch.org/whl"


def _parse_version_tuple(version_str: str) -> tuple:
    """將版本字串轉為 tuple 以便比較，例如 '12.8' -> (12, 8)"""
    parts = version_str.strip().split(".")
    return tuple(int(p) for p in parts if p.isdigit())


def _run_nvidia_smi() -> str | None:
    """
    執行 nvidia-smi 取得輸出。
    回傳 stdout 字串，失敗時回傳 None。
    """
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0:
            return result.stdout
        logger.warning("nvidia-smi 返回非零退出碼: %d", result.returncode)
        return None
    except FileNotFoundError:
        logger.info("未找到 nvidia-smi，可能沒有安裝 NVIDIA 驅動")
        return None
    except subprocess.TimeoutExpired:
        logger.warning("nvidia-smi 執行超時")
        return None
    except Exception as e:
        logger.warning("執行 nvidia-smi 時發生錯誤: %s", e)
        return None


def _parse_cuda_version(nvidia_smi_output: str) -> str | None:
    """
    從 nvidia-smi 輸出中解析 CUDA 版本。
    nvidia-smi 輸出中通常包含 'CUDA Version: XX.X' 格式的字串。
    """
    match = re.search(r"CUDA Version:\s*([\d.]+)", nvidia_smi_output)
    if match:
        return match.group(1)
    return None


def _parse_gpu_name(nvidia_smi_output: str) -> str:
    """從 nvidia-smi 輸出中解析 GPU 名稱"""
    # 嘗試從標準輸出格式中提取 GPU 名稱
    # 格式通常是 | NVIDIA GeForce RTX XXXX ... |
    match = re.search(r"\|\s+(NVIDIA\s+[^|]+?)\s+\w+\s*-", nvidia_smi_output)
    if match:
        return match.group(1).strip()

    # 備用方式：使用 --query-gpu
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0].strip()
    except Exception:
        pass

    return "Unknown NVIDIA GPU"


def _parse_driver_version(nvidia_smi_output: str) -> str:
    """從 nvidia-smi 輸出中解析驅動版本"""
    match = re.search(r"Driver Version:\s*([\d.]+)", nvidia_smi_output)
    if match:
        return match.group(1)
    return "Unknown"


def _select_pytorch_platform(cuda_version: str) -> str:
    """
    根據 CUDA 版本選擇最適合的 PyTorch compute platform。
    選擇原則：選擇 min_cuda <= 實際 CUDA 版本的最高 platform。
    """
    cuda_tuple = _parse_version_tuple(cuda_version)

    for entry in PYTORCH_PLATFORMS:
        min_tuple = _parse_version_tuple(entry["min_cuda"])
        if cuda_tuple >= min_tuple:
            return entry["platform"]

    # CUDA 版本太舊，回退到 CPU
    logger.warning("CUDA 版本 %s 太舊，沒有對應的 PyTorch GPU 版本，使用 CPU", cuda_version)
    return "cpu"


def detect_gpu_info() -> dict:
    """
    偵測 GPU 資訊並決定 PyTorch 安裝策略。

    回傳:
        dict: {
            "has_nvidia": bool,        # 是否有 NVIDIA GPU
            "gpu_name": str,           # GPU 名稱（例如 "NVIDIA GeForce RTX 4090"）
            "driver_version": str,     # 驅動版本
            "cuda_version": str,       # nvidia-smi 報告的 CUDA 版本
            "compute_platform": str,   # PyTorch compute platform（例如 "cu128"）
            "pytorch_index_url": str,  # PyTorch wheel 的 index URL
        }
    """
    result = {
        "has_nvidia": False,
        "gpu_name": "N/A",
        "driver_version": "N/A",
        "cuda_version": "N/A",
        "compute_platform": "cpu",
        "pytorch_index_url": f"{PYTORCH_INDEX_BASE}/cpu",
    }

    nvidia_smi_output = _run_nvidia_smi()
    if nvidia_smi_output is None:
        logger.info("未偵測到 NVIDIA GPU，將使用 CPU 模式")
        return result

    cuda_version = _parse_cuda_version(nvidia_smi_output)
    if cuda_version is None:
        logger.warning("無法從 nvidia-smi 輸出中解析 CUDA 版本")
        return result

    platform = _select_pytorch_platform(cuda_version)

    result.update({
        "has_nvidia": True,
        "gpu_name": _parse_gpu_name(nvidia_smi_output),
        "driver_version": _parse_driver_version(nvidia_smi_output),
        "cuda_version": cuda_version,
        "compute_platform": platform,
        "pytorch_index_url": f"{PYTORCH_INDEX_BASE}/{platform}",
    })

    logger.info(
        "GPU 偵測完成: %s, CUDA %s -> %s",
        result["gpu_name"], cuda_version, platform
    )

    return result


def get_pytorch_install_args(compute_platform: str) -> list[str]:
    """
    取得安裝 PyTorch 時需要的額外 pip 參數。

    Args:
        compute_platform: 例如 "cu128", "cpu"

    Returns:
        list: pip install 需要的額外參數列表
    """
    index_url = f"{PYTORCH_INDEX_BASE}/{compute_platform}"
    return ["--extra-index-url", index_url]


if __name__ == "__main__":
    # 獨立測試用
    logging.basicConfig(level=logging.DEBUG)
    info = detect_gpu_info()
    print("\n=== GPU 偵測結果 ===")
    for k, v in info.items():
        print(f"  {k}: {v}")
