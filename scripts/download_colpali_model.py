"""下载本地 ColPali 模型。

默认模型：vidore/colpali-v1.3
默认目录：models/colpali-v1.3
"""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    model_id = os.getenv("COLPALI_MODEL_ID", "vidore/colpali-v1.3")
    target_dir = Path(os.getenv("COLPALI_MODEL_DIR", "models/colpali-v1.3"))
    target_dir.mkdir(parents=True, exist_ok=True)

    local_path = snapshot_download(
        repo_id=model_id,
        local_dir=str(target_dir),
    )
    print(f"ColPali model downloaded: {model_id}")
    print(f"Local path: {local_path}")


if __name__ == "__main__":
    main()
