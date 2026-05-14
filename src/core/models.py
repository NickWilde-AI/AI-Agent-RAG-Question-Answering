"""
core/models.py — 兼容转发：真源见 `src/models.py`（Page / QAResult 等）

【简历链路】无独立逻辑，仅 `from ..models import *`。

【Java/Kotlin】类似 `typealias` 到单一 DTO 模块。

【Python】`from ..models import *` 通配再导出。
"""

from ..models import *  # noqa: F401,F403

