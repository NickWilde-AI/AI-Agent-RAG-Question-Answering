"""
VLM Gateway: 将项目现有 VLMClient 协议转换为 OpenAI-compatible Vision 调用。

对外接口：
- POST /answer  {query, image_paths, mode} -> {answer}
- POST /verify  {query, answer, image_paths, mode=verify} -> {verified}
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel, Field


class VLMRequest(BaseModel):
    query: str = Field(default="", min_length=0)
    image_paths: List[str] = Field(default_factory=list)
    mode: str = Field(default="single_page")
    answer: Optional[str] = None


def _guess_mime(path: str) -> str:
    s = path.lower()
    if s.endswith(".png"):
        return "image/png"
    if s.endswith(".webp"):
        return "image/webp"
    if s.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


def _to_data_url(path: str) -> str:
    p = Path(path)
    raw = p.read_bytes()
    return f"data:{_guess_mime(path)};base64,{base64.b64encode(raw).decode('utf-8')}"


def _build_client() -> OpenAI:
    api_key = os.getenv("VLM_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "EMPTY")
    base_url = os.getenv("VLM_OPENAI_BASE_URL", "http://vllm:8001/v1")
    return OpenAI(api_key=api_key, base_url=base_url)


CLIENT = _build_client()
MODEL = os.getenv("VLM_MODEL", "openbmb/MiniCPM-V-2_6")
MAX_IMAGES = int(os.getenv("VLM_MAX_IMAGES", "5"))
VERIFY_THRESHOLD = float(os.getenv("VLM_VERIFY_THRESHOLD", "0.55"))

app = FastAPI(title="VLM Gateway", version="0.1.0")


def _chat(prompt: str, image_paths: List[str]) -> str:
    content = [{"type": "text", "text": prompt}]
    for p in image_paths[:MAX_IMAGES]:
        if not Path(p).exists():
            continue
        content.append({"type": "image_url", "image_url": {"url": _to_data_url(p)}})
    resp = CLIENT.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
    )
    return (resp.choices[0].message.content or "").strip()


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}


@app.post("/answer")
def answer(req: VLMRequest):
    if req.mode == "verify":
        return verify(req)
    mode_hint = {
        "single_page": "请基于单页图像回答，优先给出精确字段。",
        "multi_page": "请综合多页图像作答，冲突时以明确证据为准。",
        "verify": "请判断答案是否被图像证据支持。",
    }.get(req.mode, "请根据图像作答。")
    prompt = (
        f"{mode_hint}\n"
        f"问题：{req.query}\n"
        "输出简洁中文答案，不要编造。"
    )
    out = _chat(prompt, req.image_paths)
    return {"answer": out}


@app.post("/verify")
def verify(req: VLMRequest):
    prompt = (
        "判断下面答案是否能从图像中直接得到证据支持。"
        "仅输出 yes 或 no。\n"
        f"问题：{req.query}\n答案：{req.answer or ''}"
    )
    out = _chat(prompt, req.image_paths).lower()
    if out.startswith("yes"):
        return {"verified": True}
    if out.startswith("no"):
        return {"verified": False}
    # 模型偶尔不按格式返回时做保守判定
    return {"verified": ("yes" in out and "no" not in out and len(out) < 40)}

