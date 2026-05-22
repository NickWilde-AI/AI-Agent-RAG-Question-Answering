"""增量建库脚本（PDF / Office / 表格 -> 页面级 JSON + 页图）。

功能：
1) 递归扫描文档目录中的 PDF / DOCX / XLSX / PPTX 等文件
2) 只处理新增/变更文件（基于 mtime + size）
3) 产出页面索引 JSON（默认 data/user_pages.json）
4) 维护 manifest（默认 data/index_manifest.json）

用法示例：
    python scripts/build_index_incremental.py \
      --input-dir user_docs \
      --output-pages data/user_pages.json \
      --manifest data/index_manifest.json \
      --image-dir kb_pages \
      --lang zh
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Tuple, Union

from src.infra.document_ingest import SUPPORTED_EXTENSIONS, ingest_document, is_supported_document
from src.models import Page


SEVEN_DOC_TYPES = [
    "report",       # 业务图表与报表
    "chart",        # 图表专项页
    "form",         # 合同与工业表单
    "infographic",  # 信息图与宣传页
    "dashboard",    # 数据看板与曲线
    "ppt",          # 培训与汇报PPT
    "manual",
]


def stable_doc_id(file_path: Path, input_dir: Path) -> str:
    """把相对路径转成稳定 doc_id，避免中文文件名或同名文件互相覆盖。"""
    try:
        relative = file_path.resolve().relative_to(input_dir.resolve())
    except ValueError:
        relative = file_path.name
    stem = str(relative.with_suffix("")).lower()
    stem = re.sub(r"[^a-z0-9_\-]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    digest = hashlib.sha1(str(relative).encode("utf-8")).hexdigest()[:8]
    return f"{stem or 'doc'}_{digest}"


def legacy_doc_id(file_path: Path) -> str:
    """兼容旧版只用文件名 stem 的 doc_id，重建时用于清理旧页面。"""
    stem = file_path.stem.lower()
    stem = re.sub(r"[^a-z0-9_\-]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem or "doc"


def infer_doc_type(file_path: Path) -> str:
    """简单规则推断 7 类文档类型。"""
    hint = f"{file_path.parent.name}_{file_path.stem}".lower()
    suffix = file_path.suffix.lower()
    if suffix in {".ppt", ".pptx"}:
        return "ppt"
    if suffix in {".xlsx", ".xls", ".csv"}:
        if any(x in hint for x in ["dashboard", "看板", "监控", "曲线", "trend", "日程", "计划"]):
            return "dashboard"
        return "form"
    if any(x in hint for x in ["dashboard", "看板", "监控", "曲线", "trend"]):
        return "dashboard"
    if any(x in hint for x in ["chart", "图表", "kpi", "柱状", "饼图"]):
        return "chart"
    if any(x in hint for x in ["form", "合同", "单据", "申请单", "发票"]):
        return "form"
    if any(x in hint for x in ["ppt", "培训", "汇报", "deck", "slide"]):
        return "ppt"
    if any(x in hint for x in ["manual", "sop", "手册", "说明书", "guide"]):
        return "manual"
    if any(x in hint for x in ["infographic", "海报", "宣传", "一图读懂"]):
        return "infographic"
    return "report"


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def file_signature(file_path: Path, doc_id: str) -> Dict[str, str]:
    stat = file_path.stat()
    return {
        "mtime": str(int(stat.st_mtime)),
        "size": str(stat.st_size),
        "doc_id": doc_id,
        "ext": file_path.suffix.lower(),
    }


def should_reindex(file_path: Path, doc_id: str, old_manifest: Dict[str, Dict[str, str]]) -> bool:
    key = str(file_path.resolve())
    new_sig = file_signature(file_path, doc_id)
    old_sig = old_manifest.get(key)
    return old_sig != new_sig


def scan_documents(input_dir: Path) -> List[Path]:
    """递归扫描支持的文档格式，大小写扩展名统一处理。"""
    return sorted(p for p in input_dir.rglob("*") if is_supported_document(p))


def summarize_extensions(files: List[Path]) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for item in files:
        ext = item.suffix.lower() or "(noext)"
        counts[ext] = counts.get(ext, 0) + 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))


def display_path(path: Union[Path, str], base_dir: Path) -> str:
    """尽量把路径打印成相对路径，终端输出更清爽。"""
    item = Path(path)
    try:
        return str(item.resolve().relative_to(base_dir.resolve()))
    except ValueError:
        return str(item)


def main() -> None:
    parser = argparse.ArgumentParser(description="增量建库脚本（PDF / DOCX / XLSX / PPTX 页面索引）")
    parser.add_argument("--input-dir", required=True, help="文档目录（递归扫描支持的文档格式）")
    parser.add_argument("--output-pages", default="data/user_pages.json", help="页面索引输出 JSON")
    parser.add_argument("--manifest", default="data/index_manifest.json", help="增量状态文件")
    parser.add_argument("--image-dir", default="kb_pages", help="页图输出目录")
    parser.add_argument("--lang", default="zh", help="文档默认语言")
    parser.add_argument("--dpi", type=int, default=200, help="PDF 渲染 DPI")
    parser.add_argument("--clean-removed", action="store_true", help="清理已删除文件对应的旧页面")
    parser.add_argument("--fail-fast", action="store_true", help="遇到单个文档解析失败时立即退出")
    parser.add_argument("--max-text-chars", type=int, default=4000, help="DOCX/TXT 每个文本块最大字符数")
    parser.add_argument("--max-sheet-rows", type=int, default=300, help="XLSX/CSV 每个 sheet 最多读取行数")
    parser.add_argument("--no-progress", action="store_true", help="关闭 tqdm 进度条（CI 或日志采集时用）")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    output_pages = Path(args.output_pages).resolve()
    manifest_path = Path(args.manifest).resolve()
    image_dir = Path(args.image_dir).resolve()

    if not input_dir.exists():
        raise FileNotFoundError("input-dir not found: %s" % input_dir)

    output_pages.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    old_pages_data = read_json(output_pages, [])
    old_manifest = read_json(manifest_path, {})

    print(
        f"[增量建库] 已载入现有索引 {len(old_pages_data)} 条页面记录；"
        "进度条按「文件」计数，单个大 PDF 解析完才会从 0% 走到下一格，并非死机。",
        flush=True,
    )

    old_pages: List[Page] = [Page(**item) for item in old_pages_data]
    doc_to_pages: Dict[str, List[Page]] = {}
    for page in old_pages:
        doc_to_pages.setdefault(page.doc_id, []).append(page)

    doc_files = scan_documents(input_dir)
    if not old_pages and doc_files and old_manifest:
        print(
            "[增量建库] 页面索引为空但 manifest 仍存在，已忽略旧 manifest，将对 user_docs 全量解析",
            flush=True,
        )
        old_manifest = {}
    active_file_keys = set(str(p.resolve()) for p in doc_files)
    new_manifest: Dict[str, Dict[str, str]] = {}

    rebuilt_doc_ids: List[str] = []
    skipped_files = 0
    failed_files: List[Tuple[str, str]] = []

    use_progress = not args.no_progress
    try:
        from tqdm import tqdm as tqdm_factory
    except ImportError:
        tqdm_factory = None  # type: ignore[misc, assignment]
        use_progress = False

    if use_progress and tqdm_factory is not None:
        progress_bar = tqdm_factory(
            doc_files,
            desc="增量建库",
            unit="文件",
            dynamic_ncols=True,
            mininterval=0.3,
        )
    else:
        progress_bar = doc_files

    for document in progress_bar:
        if tqdm_factory is not None and hasattr(progress_bar, "set_postfix_str"):
            name = document.name
            if len(name) > 48:
                name = name[:45] + "..."
            progress_bar.set_postfix_str(name, refresh=False)

        key = str(document.resolve())
        doc_id = stable_doc_id(document, input_dir)
        if not should_reindex(document, doc_id, old_manifest):
            new_manifest[key] = file_signature(document, doc_id)
            skipped_files += 1
            continue

        doc_type = infer_doc_type(document)
        print(f"[增量建库] 正在解析: {display_path(document, Path.cwd())}  （大 PDF 可能需数分钟）", flush=True)
        try:
            pages = ingest_document(
                file_path=str(document),
                doc_id=doc_id,
                doc_type=doc_type,
                language=args.lang,
                image_output_dir=str(image_dir),
                dpi=args.dpi,
                max_text_chars=args.max_text_chars,
                max_sheet_rows=args.max_sheet_rows,
            )
        except Exception as exc:
            failed_files.append((str(document), str(exc)))
            if args.fail_fast:
                raise
            print(f"\n[WARN] 跳过解析失败文档: {display_path(document, Path.cwd())} -> {exc}")
            continue

        old_doc_id = old_manifest.get(key, {}).get("doc_id")
        if old_doc_id:
            doc_to_pages.pop(old_doc_id, None)
        doc_to_pages.pop(legacy_doc_id(document), None)
        doc_to_pages[doc_id] = pages
        new_manifest[key] = file_signature(document, doc_id)
        rebuilt_doc_ids.append(doc_id)

    if tqdm_factory is not None and hasattr(progress_bar, "close"):
        progress_bar.close()

    if args.clean_removed:
        old_file_keys = set(old_manifest.keys())
        removed_keys = old_file_keys - active_file_keys
        if removed_keys:
            removed_doc_ids = {old_manifest.get(p, {}).get("doc_id") or stable_doc_id(Path(p), input_dir) for p in removed_keys}
            for rid in removed_doc_ids:
                doc_to_pages.pop(rid, None)

    merged_pages: List[Page] = []
    for _, pages in doc_to_pages.items():
        merged_pages.extend(pages)

    merged_pages.sort(key=lambda p: (p.doc_id, p.page_no or 0, p.page_id))

    output_pages.write_text(
        json.dumps([asdict(p) for p in merged_pages], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(new_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sep = "─" * 52
    print()
    print(sep)
    print("增量建库完成")
    print(sep)
    cwd = Path.cwd()
    print(f"  输入目录 : {display_path(input_dir, cwd)}")
    print(f"  支持格式 : {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    print(f"  文档总数 : {len(doc_files)}")
    print(f"  类型统计 : {', '.join(f'{ext}={count}' for ext, count in summarize_extensions(doc_files))}")
    print(f"  重建文档 : {len(rebuilt_doc_ids)}  |  跳过 : {skipped_files}  |  失败 : {len(failed_files)}")
    print(f"  页面总数 : {len(merged_pages)}")
    print(f"  页面索引 : {display_path(output_pages, cwd)}")
    print(f"  增量清单 : {display_path(manifest_path, cwd)}")
    print(f"  七类标签 : {', '.join(SEVEN_DOC_TYPES)}")
    if failed_files:
        print(sep)
        print(f"未入库（{len(failed_files)}，常见为加密 PDF，解密或换副本后再放入 user_docs）")
        for path, error in failed_files[:20]:
            print(f"  · {display_path(path, cwd)}")
            print(f"    {error}")
    print(sep)
    print()


if __name__ == "__main__":
    main()
