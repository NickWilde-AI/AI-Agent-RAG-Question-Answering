#!/usr/bin/env python3
"""用合成内容检查千问文本与视觉模型，不发送用户文档。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

from src.config import SETTINGS
from src.infra.qwen_vision_parser import QwenVisionInferenceClient, QwenVisionPageParser
from src.llm_client import LLMClient


def main() -> int:
    host=urlparse(SETTINGS.openai_base_url).hostname or "<未配置>"
    print(f"千问 API 预检：host={host} text={SETTINGS.openai_chat_model} ocr={SETTINGS.vision_parser_model} rerank={SETTINGS.qwen_vlm_rerank_model} vlm={SETTINGS.qwen_vlm_model}")
    if not SETTINGS.effective_openai_api_key:
        print("[FAIL] 未配置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY")
        return 1
    try:
        answer=LLMClient.from_settings().chat_text("只回答 OK", "连通性测试")
        if not answer: raise RuntimeError("文本模型返回空内容")
        print("[OK] 千问文本模型")
    except Exception as exc:
        print(f"[FAIL] 千问文本模型：{type(exc).__name__}: {str(exc)[:240]}")
        return 1

    try:
        import fitz
        with tempfile.TemporaryDirectory(prefix="qwen_preflight_") as folder:
            image=Path(folder)/"page.png"
            pdf=fitz.open(); page=pdf.new_page(width=500,height=300)
            page.insert_text((50,80),"Purchase Order: PO-12345",fontsize=18)
            page.insert_text((50,125),"Amount: CNY 88,000",fontsize=16)
            page.get_pixmap(dpi=96).save(str(image)); pdf.close()
            parsed=QwenVisionPageParser().parse(str(image))
            if not parsed: raise RuntimeError("视觉模型返回空内容")
        print("[OK] 千问视觉模型")
    except Exception as exc:
        print(f"[FAIL] 千问视觉模型：{type(exc).__name__}: {str(exc)[:240]}")
        return 1
    try:
        import fitz
        with tempfile.TemporaryDirectory(prefix="qwen_vlm_preflight_") as folder:
            image=Path(folder)/"page.png"; pdf=fitz.open(); page=pdf.new_page(width=500,height=300)
            page.insert_text((50,80),"Purchase Order: PO-12345",fontsize=18)
            page.get_pixmap(dpi=96).save(str(image)); pdf.close()
            answer=QwenVisionInferenceClient().answer("测试页中的采购单号是什么？",[str(image)],"single_page")
            if not answer: raise RuntimeError("在线页图模型返回空内容")
        print("[OK] 千问在线页图推理模型")
    except Exception as exc:
        print(f"[FAIL] 千问在线页图推理模型：{type(exc).__name__}: {str(exc)[:240]}")
        return 1
    try:
        import fitz
        with tempfile.TemporaryDirectory(prefix="qwen_rerank_preflight_") as folder:
            candidates=[]
            for page_id,text in (("relevant","Target Code: ZX-900"),("noise","Employee Handbook")):
                image=Path(folder)/f"{page_id}.png"; pdf=fitz.open(); page=pdf.new_page(width=500,height=300)
                page.insert_text((50,80),text,fontsize=18); page.get_pixmap(dpi=96).save(str(image)); pdf.close()
                candidates.append({"page_id":page_id,"image_path":str(image),"source_file":f"{page_id}.pdf","page_no":1})
            scores=QwenVisionInferenceClient(model=SETTINGS.qwen_vlm_rerank_model).rerank("ZX-900 是什么？",candidates)
            if scores.get("relevant",0) <= scores.get("noise",0): raise RuntimeError(f"视觉重排结果异常: {scores}")
        print("[OK] 千问候选页视觉重排模型")
    except Exception as exc:
        print(f"[FAIL] 千问候选页视觉重排模型：{type(exc).__name__}: {str(exc)[:240]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
