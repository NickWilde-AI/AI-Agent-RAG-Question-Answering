# Kafka 触发增量建库

## Topic 与 Payload

| 项 | 默认值 | 说明 |
|---|---|---|
| Topic | `doc.upload.completed` | 文档上传完成事件 |
| Consumer Group | `rag-incremental-indexer` | 见 `scripts/kafka_consumer_reindex.py` |
| DLQ 目录 | `data/kafka_dlq/` | 超过重试后落盘，便于人工回放 |

消息体（JSON）示例：

```json
{
  "event_id": "evt-20260526-001",
  "file_path": "data/uploads/report_q2.pdf",
  "file_hash": "sha256:abc...",
  "version": 3,
  "tenant_id": "default"
}
```

## 幂等

- 幂等键：`file_path` + `version`（若无 `version` 则用 `file_hash`）。
- 消费者维护已处理键集合（进程内）；生产环境建议换 Redis / DB。

## 重试与死信

- 处理失败：指数退避重试（`--max-retries`，默认 3）。
- 超过重试：写入 `data/kafka_dlq/` 下的 JSON 文件，便于人工回放。

## 启动

```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export KAFKA_DOC_UPLOAD_TOPIC=doc.upload.completed   # 兼容 KAFKA_TOPIC_DOC_UPLOADED
python scripts/kafka_consumer_reindex.py --max-retries 3 --retry-base-seconds 1
```

内部调用 `scripts/build_index_incremental.py`，与 `one_click_demo.sh` 手动增量入口共用同一套建库逻辑。
