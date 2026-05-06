"""加载环境变量：支持项目根目录与 backend 目录下的 `.env` 或 `.env.txt`（Windows 记事本常见误命名）。"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_project_dotenv() -> None:
    backend = Path(__file__).resolve().parent
    root = backend.parent
    # 先读根目录，再读 backend；后者覆盖前者。文件名优先 `.env`，其次 `.env.txt`
    for base in (root, backend):
        for name in (".env", ".env.txt"):
            path = base / name
            if path.is_file():
                load_dotenv(path, override=True)
