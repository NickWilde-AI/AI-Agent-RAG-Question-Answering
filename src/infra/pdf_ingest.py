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
from typing import List, Optional

from ..models import Page


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
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            image_path = None
            if output_dir:
                pix = page.get_pixmap(dpi=dpi)
                image_file = output_dir / f"{doc_id}_p{i}.png"
                pix.save(str(image_file))
                image_path = str(image_file)
            pages.append(
                Page(
                    page_id=f"{doc_id}_p{i}",
                    doc_id=doc_id,
                    doc_type=doc_type,
                    language=language,
                    content=text or f"(empty page {i})",
                    image_path=image_path,
                    page_no=i,
                    source_file=str(path),
                )
            )
    return pages

