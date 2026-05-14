"""
core/config.py — 兼容转发：真源见 `src/config.py`（SETTINGS / Settings）

【简历链路】无独立逻辑，仅 `from ..config import *`。

【Java/Kotlin】类似 `typealias` 到单一配置模块。

【Python】`from ..config import *` 通配再导出。
"""

from ..config import *  # noqa: F401,F403

