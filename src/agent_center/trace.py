from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def build_evidence_pages(engine: Any, qa_result: Any) -> List[Dict[str, Any]]:
    pages: List[Dict[str, Any]] = []
    seen = set()
    citations = list(getattr(qa_result, "citation_details", []) or [])
    hits = list(getattr(qa_result, "retry_hits", None) or getattr(qa_result, "hits", []) or [])
    for item in citations:
        page_id = item.get("page_id")
        if not page_id or page_id in seen:
            continue
        page = engine.retriever.get_page(page_id)
        source_file = item.get("source_file") or (
            Path(page.source_file).name if getattr(page, "source_file", None) else getattr(page, "doc_id", page_id)
        )
        raw_page_no = item.get("page_no")
        page_no = int(raw_page_no) if str(raw_page_no).isdigit() else getattr(page, "page_no", None)
        pages.append(
            {
                "page_id": page_id,
                "doc_id": item.get("doc_id") or getattr(page, "doc_id", ""),
                "source_file": source_file,
                "page_no": page_no,
                "score": float(item.get("score") or 0),
                "excerpt": item.get("excerpt", ""),
            }
        )
        seen.add(page_id)
    for hit in hits:
        page_id = getattr(hit, "page_id", "")
        if not page_id or page_id in seen:
            continue
        page = engine.retriever.get_page(page_id)
        source_file = Path(page.source_file).name if getattr(page, "source_file", None) else getattr(page, "doc_id", page_id)
        pages.append(
            {
                "page_id": page_id,
                "doc_id": getattr(page, "doc_id", ""),
                "source_file": source_file,
                "page_no": getattr(page, "page_no", None),
                "score": float(getattr(hit, "score", 0)),
                "excerpt": " ".join((getattr(page, "content", "") or "").split())[:180],
            }
        )
        seen.add(page_id)
    return pages


def build_trace_payload(qa_result: Any, include_trace: bool = True) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "branch": getattr(qa_result, "branch", ""),
        "rewritten_query": getattr(qa_result, "rewritten_query", ""),
        "verified": bool(getattr(qa_result, "verified", False)),
    }
    trace = getattr(qa_result, "trace", None)
    if include_trace and trace:
        payload["route_branch"] = getattr(trace, "route_branch", "")
        payload["fallback_triggered"] = bool(getattr(trace, "fallback_triggered", False))
        payload["retry_reason"] = getattr(trace, "retry_reason", "")
        payload["stages"] = [
            {
                "stage": stage.stage,
                "elapsed_ms": stage.elapsed_ms,
                "detail": stage.detail,
            }
            for stage in getattr(trace, "stages", []) or []
        ]
    return payload
