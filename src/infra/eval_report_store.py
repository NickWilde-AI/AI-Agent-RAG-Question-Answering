from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..eval_suite import EvalRunReport


def _report_dir() -> Path:
    return Path("reports") / "eval"


def save_eval_report(
    report: EvalRunReport,
    *,
    data_path: str,
    sample_count: int,
    tag: str = "",
) -> Path:
    """
    持久化离线评测报告，便于提测归档和回放。
    文件名按时间戳排序，天然可用于 latest 检索。
    """
    out_dir = _report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_tag = "".join(ch for ch in tag if ch.isalnum() or ch in ("-", "_")).strip("_-")
    suffix = f"-{safe_tag}" if safe_tag else ""
    out_path = out_dir / f"eval-report-{ts}{suffix}.json"
    payload: Dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "tag": tag,
        "data_path": data_path,
        "sample_count": sample_count,
        "report": asdict(report),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def load_latest_eval_report() -> Optional[Dict[str, Any]]:
    """读取最近一次评测报告（若不存在返回 None）。"""
    out_dir = _report_dir()
    if not out_dir.exists():
        return None
    files = sorted([p for p in out_dir.glob("eval-report-*.json") if p.is_file()])
    if not files:
        return None
    latest = files[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    payload["report_path"] = str(latest)
    return payload
