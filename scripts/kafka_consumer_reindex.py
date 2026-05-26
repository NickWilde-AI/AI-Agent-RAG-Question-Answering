#!/usr/bin/env python3
"""
Kafka 文档上传事件消费者：收到消息后触发增量建库。

默认环境变量：
- KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:9092
- KAFKA_TOPIC_DOC_UPLOADED=doc.upload.completed（兼容 KAFKA_DOC_UPLOAD_TOPIC）
- KAFKA_GROUP_ID=rag-incremental-indexer
- KAFKA_AUTO_OFFSET_RESET=latest

消息体建议：
{
  "event_id": "uuid",
  "file_path": "user_docs/foo.pdf",
  "version": "2026-05-26T14:00:00"
}
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = ROOT / "data" / "kafka_reindex_state.json"
DEFAULT_DLQ_DIR = ROOT / "data" / "kafka_dlq"


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"processed_keys": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_keys": []}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def event_key(payload: Dict[str, Any]) -> str:
    file_path = str(payload.get("file_path") or "").strip()
    version = str(payload.get("version") or "").strip()
    file_hash = str(payload.get("file_hash") or payload.get("hash") or "").strip()
    if file_path and (version or file_hash):
        return f"{file_path}::{version or file_hash}"
    for k in ("event_id", "id", "message_id"):
        if payload.get(k):
            return str(payload[k])
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def run_incremental_build(input_dir: str = "user_docs") -> int:
    cmd = [
        sys.executable,
        "scripts/build_index_incremental.py",
        "--input-dir",
        input_dir,
        "--output-pages",
        "data/user_pages.json",
        "--manifest",
        "data/index_manifest.json",
        "--image-dir",
        "kb_pages",
        "--lang",
        "zh",
        "--clean-removed",
    ]
    print(f"[kafka-reindex] run: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
    return int(proc.returncode)


def trim_processed_keys(processed: Set[str], max_size: int = 5000) -> Set[str]:
    if len(processed) <= max_size:
        return processed
    trimmed: List[str] = sorted(processed)[-max_size:]
    return set(trimmed)


def write_dlq(dlq_dir: Path, key: str, payload: Dict[str, Any], last_code: int, attempts: int) -> Path:
    dlq_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in key)[:80]
    out = dlq_dir / f"dlq-{ts}-{safe_key}.json"
    body = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "key": key,
        "attempts": attempts,
        "last_exit_code": last_code,
        "payload": payload,
    }
    out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def process_with_retry(input_dir: str, max_retries: int, retry_base_seconds: float) -> int:
    total = max(1, max_retries + 1)
    last_code = 1
    for attempt in range(1, total + 1):
        last_code = run_incremental_build(input_dir=input_dir)
        if last_code == 0:
            return 0
        if attempt < total:
            wait_s = min(retry_base_seconds * (2 ** (attempt - 1)), 30.0)
            print(
                f"[kafka-reindex] retry attempt={attempt}/{total - 1} wait_s={wait_s:.1f} last_exit={last_code}",
                flush=True,
            )
            time.sleep(wait_s)
    return last_code


def build_consumer():
    try:
        from kafka import KafkaConsumer
    except Exception as exc:
        raise RuntimeError("缺少 kafka-python 依赖，请先 pip install kafka-python") from exc
    servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
    topic = os.getenv("KAFKA_TOPIC_DOC_UPLOADED") or os.getenv("KAFKA_DOC_UPLOAD_TOPIC") or "doc.upload.completed"
    group_id = os.getenv("KAFKA_GROUP_ID", "rag-incremental-indexer")
    auto_offset_reset = os.getenv("KAFKA_AUTO_OFFSET_RESET", "latest")
    return KafkaConsumer(
        topic,
        bootstrap_servers=[x.strip() for x in servers.split(",") if x.strip()],
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset=auto_offset_reset,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        consumer_timeout_ms=1000,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Kafka consumer for incremental indexing")
    ap.add_argument("--state-file", default=str(DEFAULT_STATE), help="状态文件路径")
    ap.add_argument("--once", action="store_true", help="仅消费一条消息后退出")
    ap.add_argument("--input-dir", default="user_docs", help="增量建库输入目录")
    ap.add_argument("--max-retries", type=int, default=3, help="建库失败最大重试次数（不含首次）")
    ap.add_argument("--retry-base-seconds", type=float, default=1.0, help="指数退避基数秒")
    ap.add_argument("--dlq-dir", default=str(DEFAULT_DLQ_DIR), help="死信目录")
    args = ap.parse_args()

    state_path = Path(args.state_file).resolve()
    dlq_dir = Path(args.dlq_dir).resolve()
    state = load_state(state_path)
    processed: Set[str] = set(state.get("processed_keys", []))

    consumer = build_consumer()
    print("[kafka-reindex] consumer started", flush=True)

    handled = 0
    try:
        while True:
            records = consumer.poll(timeout_ms=1000)
            if not records:
                if args.once and handled > 0:
                    break
                continue
            for _, msgs in records.items():
                for msg in msgs:
                    payload = msg.value if isinstance(msg.value, dict) else {}
                    key = event_key(payload)
                    if key in processed:
                        print(f"[kafka-reindex] skip duplicated key={key}", flush=True)
                        consumer.commit()
                        continue
                    t0 = time.time()
                    code = process_with_retry(
                        input_dir=args.input_dir,
                        max_retries=max(0, args.max_retries),
                        retry_base_seconds=max(0.1, args.retry_base_seconds),
                    )
                    cost = int((time.time() - t0) * 1000)
                    if code == 0:
                        print(f"[kafka-reindex] ok key={key} cost_ms={cost}", flush=True)
                        processed.add(key)
                        processed = trim_processed_keys(processed, max_size=5000)
                        save_state(state_path, {"processed_keys": list(processed)})
                        consumer.commit()
                    else:
                        dlq_file = write_dlq(
                            dlq_dir=dlq_dir,
                            key=key,
                            payload=payload,
                            last_code=code,
                            attempts=max(1, args.max_retries + 1),
                        )
                        print(
                            f"[kafka-reindex] failed key={key} exit={code} cost_ms={cost} dlq={dlq_file}",
                            flush=True,
                        )
                        # 失败事件写入死信后提交 offset，避免卡住消费
                        consumer.commit()
                    handled += 1
                    if args.once:
                        return 0 if code == 0 else 1
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

