"""SQLite 连接管理

每次操作独立连接、独立提交、独立关闭，避免多线程共用连接的线程安全问题。
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# 数据库文件位置：环境变量 PET_HOSPITAL_DB 指定，默认项目根目录
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "pet_hospital.db"
DB_PATH = Path(os.environ.get("PET_HOSPITAL_DB", str(_DEFAULT_DB)))

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS pets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  species     TEXT NOT NULL,
  breed       TEXT,
  age         REAL,
  owner_name  TEXT NOT NULL,
  owner_phone TEXT NOT NULL,
  created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS medical_records (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pet_id       INTEGER NOT NULL,
  visit_date   TEXT NOT NULL,
  symptoms     TEXT NOT NULL,
  diagnosis    TEXT,
  prescription TEXT,
  doctor       TEXT,
  created_at   TEXT DEFAULT (datetime('now','localtime')),
  FOREIGN KEY (pet_id) REFERENCES pets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pet  ON medical_records(pet_id);
CREATE INDEX IF NOT EXISTS idx_date ON medical_records(visit_date);
"""


@contextmanager
def get_conn():
    """获取数据库连接的上下文管理器

    每次调用独立创建连接，设置 busy_timeout=30s 防并发写入冲突，
    操作结束后自动 commit 并 close。
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """幂等建表，每个对外函数入口调用，保证表已存在"""
    with get_conn() as conn:
        conn.executescript(_CREATE_SQL)
