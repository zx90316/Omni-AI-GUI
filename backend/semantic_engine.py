# -*- coding: utf-8 -*-
"""
Semantic Engine (Embedding & Reranking)
將原本 BGE_Api.py 中對應模型載入、資源管理、隊列處理整理至此
"""
import os
import sys
import asyncio
import logging
import gc
import threading
from dataclasses import dataclass
from typing import Optional, Dict, Any
from asyncio import Queue, Future
import traceback

import torch
import psutil
import numpy as np
from contextlib import asynccontextmanager

# 如果本機沒有安裝 FlagEmbedding，可以加上 try...except 容錯
try:
    from FlagEmbedding import FlagReranker, BGEM3FlagModel
except ImportError:
    FlagReranker = None
    BGEM3FlagModel = None

logger = logging.getLogger(__name__)

# ================== 資源管理配置 ==================
@dataclass
class ResourceConfig:
    max_gpu_memory_usage: float = 0.85
    max_queue_size: int = 1000
    request_timeout: float = 120.0
    batch_size: int = 5
    cleanup_interval: int = 50
    resource_check_interval: int = 100

config = ResourceConfig()

# ================== 線程安全的模型容器 ==================
class ModelContainer:
    """線程安全的模型容器"""
    def __init__(self):
        self._lock = threading.RLock()
        self._reranker = None
        self._embedding_model = None
        self._models_loaded = False
        
    @property
    def reranker(self):
        with self._lock:
            return self._reranker
    
    @reranker.setter
    def reranker(self, value):
        with self._lock:
            self._reranker = value
    
    @property
    def embedding_model(self):
        with self._lock:
            return self._embedding_model
    
    @embedding_model.setter
    def embedding_model(self, value):
        with self._lock:
            self._embedding_model = value
    
    @property
    def models_loaded(self) -> bool:
        with self._lock:
            return self._models_loaded and (self._reranker is not None or self._embedding_model is not None)
            
    def mark_loaded(self):
        with self._lock:
            self._models_loaded = True
            
    def cleanup(self):
        with self._lock:
            if self._reranker is not None:
                try:
                    del self._reranker
                except Exception as e:
                    logger.warning(f"清理 reranker 發生警告: {e}")
                self._reranker = None
            
            if self._embedding_model is not None:
                try:
                    del self._embedding_model
                except Exception as e:
                    logger.warning(f"清理 embedding_model 發生警告: {e}")
                self._embedding_model = None
            
            self._models_loaded = False

model_container = ModelContainer()
request_queue: Queue = asyncio.Queue(maxsize=config.max_queue_size)
_worker_task: Optional[asyncio.Task] = None

# ================== 記憶體清理 ==================
def cleanup_gpu_memory():
    try:
        if torch.cuda.is_available():
            gc.collect()
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, 'reset_peak_memory_stats'):
                try:
                    torch.cuda.reset_peak_memory_stats()
                except Exception:
                    pass
    except Exception as e:
        pass

def efficient_cleanup_gpu_memory():
    try:
        if torch.cuda.is_available():
            gc.collect()
            torch.cuda.empty_cache()
    except Exception as e:
        pass

async def async_cleanup_gpu_memory():
    await asyncio.to_thread(cleanup_gpu_memory)

async def async_efficient_cleanup_gpu_memory():
    await asyncio.to_thread(efficient_cleanup_gpu_memory)

def check_system_resources() -> Dict[str, Any]:
    try:
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        
        gpu_info = {}
        if torch.cuda.is_available():
            try:
                device = torch.cuda.current_device()
                memory_allocated = torch.cuda.memory_allocated(device) / (1024**3)
                memory_reserved = torch.cuda.memory_reserved(device) / (1024**3)
                memory_total = torch.cuda.get_device_properties(device).total_memory / (1024**3)
                gpu_memory_usage = memory_reserved / memory_total if memory_total > 0 else 0
                
                gpu_info = {
                    "memory_usage_percent": round(gpu_memory_usage * 100, 2)
                }
            except Exception:
                pass
                
        return {
            "cpu_percent": cpu_percent,
            "gpu_info": gpu_info
        }
    except Exception as e:
        return {}

# ================== 背景隊列處理 ==================
def process_embeddings_in_batches(sentences, batch_size=None):
    if batch_size is None:
        batch_size = config.batch_size
    if not sentences:
        return np.array([])
    
    all_embeddings = []
    try:
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i:i + batch_size]
            if not batch:
                continue
            
            with torch.no_grad():
                batch_embeddings = model_container.embedding_model.encode(
                    batch, 
                    return_dense=True, 
                    return_sparse=False, 
                    return_colbert_vecs=False,
                    batch_size=len(batch)
                )['dense_vecs']
                all_embeddings.append(batch_embeddings)
            
            if (i // batch_size) % (config.cleanup_interval * 2) == (config.cleanup_interval * 2 - 1):
                efficient_cleanup_gpu_memory()
        
        if all_embeddings:
            res = np.vstack(all_embeddings)
            return res
        return np.array([])
    except Exception as e:
        efficient_cleanup_gpu_memory()
        raise

async def gpu_processor_worker():
    logger.info("Semantic GPU 工作者已啟動。")
    batch_count = 0
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            if batch_count % config.resource_check_interval == 0 and batch_count > 0:
                resources = check_system_resources()
                if resources and "gpu_info" in resources:
                    gpu_usage = resources["gpu_info"].get("memory_usage_percent", 0)
                    if gpu_usage > config.max_gpu_memory_usage * 100:
                        asyncio.create_task(async_cleanup_gpu_memory())
            
            target_model, data, future = await request_queue.get()
            
            if future.cancelled():
                request_queue.task_done()
                continue
                
            try:
                with torch.no_grad():
                    if target_model == 'reranker':
                        if model_container.reranker is None:
                            raise RuntimeError("Reranker 模型未載入")
                        
                        def rerank_blocking_call():
                            pairs = data['pairs']
                            normalize = data.get('normalize', True)
                            result = model_container.reranker.compute_score(
                                pairs,
                                batch_size=config.batch_size, 
                                normalize=normalize
                            )
                            return result
                        
                        result = await asyncio.to_thread(rerank_blocking_call)
                        if not future.cancelled():
                            future.set_result(result)
                            
                    elif target_model == 'embedding':
                        if model_container.embedding_model is None:
                            raise RuntimeError("Embedding 模型未載入")
                        
                        def embed_blocking_call():
                            sentences = data['sentences']
                            embeddings = process_embeddings_in_batches(sentences)
                            return embeddings.tolist()
                            
                        result = await asyncio.to_thread(embed_blocking_call)
                        if not future.cancelled():
                            future.set_result(result)
                    else:
                        raise ValueError(f"未知的模型目標: {target_model}")
                        
                batch_count += 1
                if batch_count % config.cleanup_interval == 0:
                    asyncio.create_task(async_efficient_cleanup_gpu_memory())
                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Semantic 背景請求處理錯誤: {e}")
                if not future.cancelled():
                    future.set_exception(e)
                
                if consecutive_errors >= max_consecutive_errors:
                    asyncio.create_task(async_cleanup_gpu_memory())
                    await asyncio.sleep(1)
                    consecutive_errors = 0
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            await asyncio.sleep(5)
        finally:
            try:
                request_queue.task_done()
            except ValueError:
                pass


# ================== 生命週期管理與載入 ==================
def _is_network_error(e: Exception) -> bool:
    err_str = str(e).lower()
    return any(kw in err_str for kw in ["connection", "proxy", "timeout", "resolve", "offline"])


def init_semantic_models():
    """初始化並載入 Semantic Models (Reranker, Embedding)，含離線保護"""
    if not FlagReranker or not BGEM3FlagModel:
        logger.warning("未安裝 FlagEmbedding。請使用 `pip install FlagEmbedding` 來啟用 Semantic 引擎。")
        return False

    from backend.network_utils import is_offline_mode, is_model_cached, make_offline_error_message

    try:
        import torch
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        
        if torch.cuda.is_available():
            torch.backends.cudnn.benchmark = True
            torch.backends.cudnn.enabled = True
            os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:128'
        
        offline = is_offline_mode()

        # 1. Reranker
        reranker_id = "BAAI/bge-reranker-v2-m3"
        logger.info(f"正在載入 Reranker 模型 {reranker_id} (若無快取將自動下載)...")
        try:
            if offline and not is_model_cached(reranker_id):
                logger.warning(make_offline_error_message(reranker_id))
            else:
                reranker = FlagReranker(reranker_id, use_fp16=True, device=device)
                if hasattr(reranker, 'model'):
                    reranker.model.eval()
                    for param in reranker.model.parameters():
                        param.requires_grad = False
                model_container.reranker = reranker
                logger.info("✅ Reranker 模型載入成功")
        except Exception as e:
            if _is_network_error(e):
                logger.warning(make_offline_error_message(reranker_id))
            else:
                logger.error(f"Reranker 模型載入或下載失敗: {e}")
            
        # 2. Embedding
        embedding_id = "BAAI/bge-m3"
        logger.info(f"正在載入 Embedding 模型 {embedding_id} (若無快取將自動下載)...")
        try:
            if offline and not is_model_cached(embedding_id):
                logger.warning(make_offline_error_message(embedding_id))
            else:
                embedding_model = BGEM3FlagModel(embedding_id, use_fp16=True, device=device)
                if hasattr(embedding_model, 'model'):
                    embedding_model.model.eval()
                    for param in embedding_model.model.parameters():
                        param.requires_grad = False
                model_container.embedding_model = embedding_model
                logger.info("✅ Embedding 模型載入成功")
        except Exception as e:
            if _is_network_error(e):
                logger.warning(make_offline_error_message(embedding_id))
            else:
                logger.error(f"Embedding 模型載入或下載失敗: {e}")
            
            
        if model_container.reranker or model_container.embedding_model:
            model_container.mark_loaded()
            return True
            
        return False
    except Exception as e:
        logger.error(f"Semantic 模型載入失敗: {e}", exc_info=True)
        return False

def start_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        try:
            loop = asyncio.get_running_loop()
            _worker_task = loop.create_task(gpu_processor_worker())
        except RuntimeError:
            logger.warning("無法啟動 Semantic worker (no running event loop)")

def stop_worker_and_cleanup():
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
    model_container.cleanup()
    cleanup_gpu_memory()
