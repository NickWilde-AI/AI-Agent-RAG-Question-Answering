"""
pdf_ingest.py — PDF 建库骨架：PyMuPDF 读页 → `Page` 列表（文本 + 可选页图路径）

================================================================================
【在「简历第一条」里的位置】
================================================================================
- 属于 **L0 离线建库** 子链路：在 `retriever._build_index` 之前，先把 PDF 变成「页」对象；简历里「PyMuPDF 渲染页图」落点在此。
- 在线问答路径**不必经**本文件；只有跑 ingest 脚本或扩展建库时会调用。

================================================================================
【类比 Android】
================================================================================
- 像 **PdfRenderer + Bitmap 导出**：每页一张图 + 抽取文字，再交给后续 pipeline。

================================================================================
【从 Java/Kotlin 读 Python】
================================================================================
- `Optional[str] = None`：可选输出目录；Kotlin `String? = null`。
- `-> List[Page]`：返回类型注解，便于 IDE 导航。

PDF 入库链路：PyMuPDF 渲染 -> 页面级数据结构。
"""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from ..config import SETTINGS
from ..models import Page
from .qwen_vision_parser import QwenVisionPageParser


def ingest_pdf_with_pymupdf(
    pdf_path: str,
    doc_id: str,
    doc_type: str = "manual",
    language: str = "zh",
    image_output_dir: Optional[str] = None,
    dpi: int = 200,
) -> List[Page]:
    """
    用 PyMuPDF 读取 PDF 并产出页面级数据结构。

    说明：
    - 这是“建库链路入口骨架”，可对接 OCR、图像 embedding、Milvus 入库
    - 若本地没装 pymupdf，会抛出可读错误（避免静默失败）
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import fitz  # type: ignore  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF is required. Install 'pymupdf' to use PDF ingest.") from exc

    output_dir = Path(image_output_dir) if image_output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    pages: List[Page] = []
    vision_candidates: List[Tuple[Page, str]] = []
    vision_parser = QwenVisionPageParser()
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            image_path = None
            if output_dir:
                pix = page.get_pixmap(dpi=dpi)
                image_file = output_dir / f"{doc_id}_p{i}.png"
                pix.save(str(image_file))
                image_path = str(image_file)
            image_count = len(page.get_images(full=True))
            try:
                drawing_count = len(page.get_drawings())
            except Exception:
                drawing_count = 0
            item = Page(
                    page_id=f"{doc_id}_p{i}",
                    doc_id=doc_id,
                    doc_type=doc_type,
                    language=language,
                    content=text or f"(empty page {i})",
                    image_path=image_path,
                    page_no=i,
                    source_file=str(path),
                    metadata={
                        "local_text_chars": len(text),
                        "embedded_image_count": image_count,
                        "drawing_count": drawing_count,
                    },
                )
            pages.append(item)
            mode = SETTINGS.vision_parse_mode
            should_parse = mode == "all" or (
                mode == "auto"
                and (
                    len(text) < SETTINGS.vision_min_text_chars
                    or image_count > 0
                    or drawing_count >= SETTINGS.vision_drawing_threshold
                )
            )
            if vision_parser.enabled and image_path and should_parse:
                vision_candidates.append((item, text))

    if vision_candidates:
        workers = max(1, min(SETTINGS.vision_parser_workers, len(vision_candidates)))
        print(
            f"[千问VL解析] {path.name}: {len(vision_candidates)}/{len(pages)} 页，workers={workers}",
            flush=True,
        )

        def enrich(target: Page, local_text: str) -> Tuple[Page, str]:
            return target, vision_parser.parse(target.image_path or "", local_text)

        def apply_result(target: Page, visual_text: str) -> None:
            if visual_text:
                local = target.content if not target.content.startswith("(empty page") else ""
                target.content = (local + "\n\n[千问VL页面解析]\n" + visual_text).strip()
                target.metadata["vision_parser"] = SETTINGS.vision_parser_model
                target.metadata["vision_parse_status"] = "success"

        # 首页先做连通性探测；认证/模型配置错误时立即停止，避免整份文档重复失败。
        first_target, first_local = vision_candidates[0]
        try:
            apply_result(first_target, vision_parser.parse(first_target.image_path or "", first_local))
        except Exception as exc:
            for target, _ in vision_candidates:
                target.metadata["vision_parse_status"] = "fallback"
                target.metadata["vision_parse_error"] = type(exc).__name__
            print(
                f"[WARN] 千问VL不可用，本文件保留本地解析：{type(exc).__name__}；请检查 DASHSCOPE_API_KEY、模型名和 Base URL。",
                flush=True,
            )
            return pages

        remaining = vision_candidates[1:]
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="qwen-vision") as pool:
            futures = {pool.submit(enrich, target, local): target for target, local in remaining}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    _, visual_text = future.result()
                    apply_result(target, visual_text)
                except Exception as exc:
                    target.metadata["vision_parse_status"] = "fallback"
                    target.metadata["vision_parse_error"] = type(exc).__name__
                    print(f"[WARN] 千问VL解析失败，保留本地文本: {target.page_id} ({type(exc).__name__})", flush=True)
    return pages
