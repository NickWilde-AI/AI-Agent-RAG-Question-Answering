"""本地 ColPali rerank 服务。

启动方式：
    uvicorn scripts.colpali_rerank_service:app --host 127.0.0.1 --port 9001

依赖：
    pip install -r requirements/colpali.txt
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI
from pydantic import BaseModel, Field


class RerankPage(BaseModel):
    page_id: str
    image_path: str
    doc_id: Optional[str] = None


class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1)
    pages: List[RerankPage] = Field(default_factory=list)


class RerankResponse(BaseModel):
    scores: Dict[str, float]


app = FastAPI(title="Local ColPali Rerank Service", version="0.1.0")


def _default_torch_device() -> str:
    """未设置 COLPALI_DEVICE 时：云端 GPU 用 cuda，本机 Mac 用 mps，否则 cpu。"""
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def model_status() -> Tuple[str, str, str]:
    """检查本地 ColPali 权重是否完整，避免首次请求时长时间阻塞后才失败。"""
    model_dir = Path(os.getenv("COLPALI_MODEL_DIR", "models/colpali-v1.3"))
    required_any = [model_dir / "adapter_model.safetensors", model_dir / "adapter_model.bin"]
    has_adapter = any(p.exists() and p.stat().st_size > 0 for p in required_any)
    has_config = (model_dir / "adapter_config.json").exists()
    if not model_dir.exists():
        return "missing", str(model_dir), "model directory not found"
    if not has_config:
        return "incomplete", str(model_dir), "adapter_config.json not found"
    if not has_adapter:
        return "incomplete", str(model_dir), "adapter weights not found"
    return "ready", str(model_dir), ""


@lru_cache(maxsize=1)
def load_colpali() -> tuple[object, object, object]:
    """懒加载 ColPali，避免服务启动时直接占用模型资源。"""
    import torch
    from colpali_engine.models import ColPali, ColPaliProcessor

    model_dir = os.getenv("COLPALI_MODEL_DIR", "models/colpali-v1.3")
    status, _, detail = model_status()
    if status != "ready":
        raise RuntimeError(f"ColPali local model is {status}: {detail}")
    device = os.getenv("COLPALI_DEVICE") or _default_torch_device()
    dtype_name = os.getenv("COLPALI_DTYPE", "bfloat16")
    torch_dtype = torch.bfloat16 if dtype_name == "bfloat16" else torch.float16

    model = ColPali.from_pretrained(
        model_dir,
        torch_dtype=torch_dtype,
        device_map=device,
        local_files_only=True,
    ).eval()
    processor = ColPaliProcessor.from_pretrained(model_dir, local_files_only=True)
    return model, processor, torch


@app.get("/health")
def health() -> Dict[str, str]:
    status, model_dir, detail = model_status()
    return {"status": "ok", "model_status": status, "model_dir": model_dir, "detail": detail}


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    """输入 query 和候选页图，返回每个 page_id 的 ColPali 分数。"""
    if not req.pages:
        return RerankResponse(scores={})

    status, _, detail = model_status()
    if status != "ready":
        # 模型权重不完整时快速降级，主 API 会保留原召回分数，避免公网请求变成 502。
        return RerankResponse(scores={})

    from PIL import Image

    try:
        model, processor, torch = load_colpali()
    except Exception:
        return RerankResponse(scores={})
    valid_pages: List[RerankPage] = []
    images = []
    for page in req.pages:
        path = Path(page.image_path)
        if not path.exists():
            continue
        valid_pages.append(page)
        images.append(Image.open(path).convert("RGB"))

    if not valid_pages:
        return RerankResponse(scores={})

    batch_images = processor.process_images(images).to(model.device)
    batch_queries = processor.process_queries([req.query]).to(model.device)

    with torch.no_grad():
        image_embeddings = model(**batch_images)
        query_embeddings = model(**batch_queries)
        score_matrix = processor.score_multi_vector(query_embeddings, image_embeddings)

    scores = score_matrix[0].detach().float().cpu().tolist()
    return RerankResponse(scores={page.page_id: float(score) for page, score in zip(valid_pages, scores)})
