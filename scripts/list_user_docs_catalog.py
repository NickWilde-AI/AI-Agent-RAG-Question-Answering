#!/usr/bin/env python3
"""
扫描 user_docs（或指定目录）下的文件，按扩展名大类分组，写入清单文本。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path


def bucket_for_suffix(suffix: str) -> str:
    s = suffix.lower()
    if s == ".pdf":
        return "PDF"
    if s in (".doc", ".docx"):
        return "Word"
    if s in (".xls", ".xlsx", ".csv"):
        return "表格"
    if s in (".ppt", ".pptx"):
        return "演示"
    if s in (".txt", ".md", ".markdown"):
        return "文本"
    if s in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        return "图片"
    if not s or s == ".":
        return "无扩展名"
    return "其他"


def main() -> None:
    ap = argparse.ArgumentParser(description="列出输入目录中的文档并按类型分组")
    ap.add_argument("--input-dir", default="user_docs", type=Path, help="待扫描目录（默认 user_docs）")
    ap.add_argument(
        "--output",
        default="data/user_docs_catalog.txt",
        type=Path,
        help="输出清单路径（默认 data/user_docs_catalog.txt）",
    )
    args = ap.parse_args()
    root: Path = args.input_dir
    out: Path = args.output

    if not root.is_dir():
        raise SystemExit(f"目录不存在或不是文件夹: {root}")

    by_bucket: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root)
            by_bucket[bucket_for_suffix(p.suffix)].append(rel)

    lines: list[str] = []
    lines.append(f"# user_docs 文档清单（相对 {root}/）")
    lines.append("")
    order = ["PDF", "Word", "表格", "演示", "文本", "图片", "其他", "无扩展名"]
    order_set = set(order)
    for name in order:
        if name not in by_bucket:
            continue
        paths = sorted(by_bucket[name], key=lambda x: str(x).lower())
        lines.append(f"## {name}（{len(paths)}）")
        for rel in paths:
            lines.append(f"- {rel.as_posix()}")
        lines.append("")

    for name in sorted(set(by_bucket.keys()) - order_set):
        paths = sorted(by_bucket[name], key=lambda x: str(x).lower())
        lines.append(f"## {name}（{len(paths)}）")
        for rel in paths:
            lines.append(f"- {rel.as_posix()}")
        lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines).rstrip() + "\n"
    out.write_text(text, encoding="utf-8")
    print(f"已写入 {out}（共 {sum(len(v) for v in by_bucket.values())} 个文件）")


if __name__ == "__main__":
    main()
