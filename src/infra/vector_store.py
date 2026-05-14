"""
vector_store.py — 向量存储抽象：内存实现 + Milvus 实现（可切换）

================================================================================
【在「简历第一条：检索 → 路由 → 生成 → 校验 → 重试」里的位置】
================================================================================
- 被 `PageRetriever._build_vector_store` 选择：`SETTINGS.vector_backend == "milvus"` 时尝试 Milvus，失败回退内存。
- `upsert`：建索引时写入向量；`search`：在线检索 top-k（返回 id + score）。

================================================================================
【类比 Android】
================================================================================
- `BaseVectorStore` ≈ **Java `interface VectorStore`**；`InMemoryVectorStore` / `MilvusVectorStore` ≈ 两种 `implements`。
- 策略切换像 **Debug 用 FakeRepository，Release 用 RealRepository**。

================================================================================
【从 Java/Kotlin 读 Python：本文件用到的语法】
================================================================================
- `raise NotImplementedError`：抽象方法无 `@abstractmethod` 装饰时，子类忘了实现会在运行期调用时报错（也可用 ABC）。
- `Sequence[float]`：`typing` 协议「只读序列」，比 `List[float]` 更宽，接受 tuple / array 视图。
- `lambda x: x[1]`：`sort` 的 key 函数，取 tuple 第二个元素做排序键；Kotlin `sortedBy { it.second }`。
- `from pymilvus import MilvusClient` 放在 `__init__` 的 try 里：**延迟 import**，没装依赖时错误信息更友好。

向量存储抽象：支持 InMemory 与 Milvus（可选依赖）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple


class BaseVectorStore:
    """向量库统一接口。"""

    def upsert(self, item_id: str, vector: Sequence[float]) -> None:
        raise NotImplementedError

    def search(self, vector: Sequence[float], topk: int) -> List[Tuple[str, float]]:
        raise NotImplementedError


@dataclass
class InMemoryVectorStore(BaseVectorStore):
    """内存版向量库，便于本地和面试演示。"""

    vectors: Dict[str, List[float]] = field(default_factory=dict)

    def upsert(self, item_id: str, vector: Sequence[float]) -> None:
        self.vectors[item_id] = list(vector)

    def search(self, vector: Sequence[float], topk: int) -> List[Tuple[str, float]]:
        q = list(vector)
        scored: List[Tuple[str, float]] = []
        for item_id, v in self.vectors.items():
            dim = min(len(q), len(v))
            score = sum(q[i] * v[i] for i in range(dim))
            scored.append((item_id, float(score)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:topk]


class MilvusVectorStore(BaseVectorStore):
    """
    Milvus 向量库封装（可选）。

    如果环境没有安装 pymilvus，则自动抛出可读错误，提醒用户切回 inmemory。
    """

    def __init__(self, uri: str, token: str, collection: str, dim: int) -> None:
        self.uri = uri
        self.token = token
        self.collection = collection
        self.dim = dim
        try:
            from pymilvus import MilvusClient  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Milvus backend requires pymilvus. "
                "Install dependency or set RAG_VECTOR_BACKEND=inmemory."
            ) from exc
        self.client = MilvusClient(uri=uri, token=token or None)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self.client.has_collection(self.collection):
            return
        self.client.create_collection(collection_name=self.collection, dimension=self.dim)

    def upsert(self, item_id: str, vector: Sequence[float]) -> None:
        self.client.upsert(
            collection_name=self.collection,
            data=[{"id": item_id, "vector": list(vector)}],
        )

    def search(self, vector: Sequence[float], topk: int) -> List[Tuple[str, float]]:
        res = self.client.search(
            collection_name=self.collection,
            data=[list(vector)],
            limit=topk,
            output_fields=["id"],
        )
        hits = res[0] if res else []
        return [(str(hit.get("id")), float(hit.get("distance", 0.0))) for hit in hits]

