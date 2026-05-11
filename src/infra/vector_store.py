"""向量存储抽象：支持 InMemory 与 Milvus（可选依赖）。"""

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

