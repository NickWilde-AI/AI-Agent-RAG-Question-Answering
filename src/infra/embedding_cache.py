"""本地持久化 embedding 缓存，避免服务重启后重复请求远程模型。"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import List, Optional


class JSONEmbeddingCache:
    def __init__(self,path: str,max_entries: int = 20000) -> None:
        self.path=Path(path); self.max_entries=max(100,max_entries); self._lock=threading.RLock(); self._dirty=False
        try:
            payload=json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            self._items={str(k):[float(x) for x in v] for k,v in payload.items() if isinstance(v,list)}
        except Exception:
            self._items={}

    @staticmethod
    def key(base_url: str,model: str,text: str) -> str:
        return hashlib.sha256(f"{base_url}\0{model}\0{text}".encode("utf-8")).hexdigest()

    def get(self,key: str) -> Optional[List[float]]:
        with self._lock:
            value=self._items.get(key)
            return list(value) if value is not None else None

    def put(self,key: str,value: List[float]) -> None:
        with self._lock:
            self._items[key]=[float(x) for x in value]; self._dirty=True
            while len(self._items)>self.max_entries: self._items.pop(next(iter(self._items)))

    def save(self) -> None:
        with self._lock:
            if not self._dirty: return
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix(self.path.suffix+".tmp")
            tmp.write_text(json.dumps(self._items,separators=(",",":")),encoding="utf-8")
            tmp.replace(self.path); self._dirty=False
