"""SQLAlchemy engine、会话工厂与依赖注入用的 ``get_db``。"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

_backend_dir = os.path.dirname(os.path.abspath(__file__))
_default_sqlite = f"sqlite:///{os.path.join(_backend_dir, 'guitar_search.db')}"

DATABASE_URL = os.getenv("DATABASE_URL", _default_sqlite).strip() or _default_sqlite

# SQLite 需 ``check_same_thread=False`` 供 FastAPI 多线程 worker 使用
_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """创建缺失的表（启动时调用）。须先导入 ORM 模型以注册 metadata。"""
    from models import Favorite  # noqa: F401
    from models import User  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
