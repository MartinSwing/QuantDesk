# -*- coding: utf-8 -*-
"""
公共组件：
- Database: 基于 queue 的轻量 MySQL 连接池（DictCursor，自动提交/回滚）
- login_manager: Flask-Login
"""
import queue
import threading
from contextlib import contextmanager

import pymysql
import pymysql.cursors
from flask_login import LoginManager

from config import settings


class Database:
    """轻量连接池：适用于单机 Flask（threaded=True）场景"""

    def __init__(self, pool_size: int = 8):
        self._pool = queue.Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        for _ in range(pool_size):
            self._pool.put(self._create_conn())

    def _create_conn(self):
        return pymysql.connect(
            **settings.MYSQL_CONFIG,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
        )

    @contextmanager
    def connection(self):
        conn = self._pool.get(timeout=10)
        try:
            conn.ping(reconnect=True)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.put(conn)

    # ---------- 快捷方法 ----------
    def fetch_all(self, sql, params=None):
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                return cur.fetchall()

    def fetch_one(self, sql, params=None):
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                return cur.fetchone()

    def execute(self, sql, params=None) -> int:
        """单条写操作，返回受影响行数"""
        with self.connection() as conn:
            with conn.cursor() as cur:
                return cur.execute(sql, params or ())

    def executemany(self, sql, seq) -> int:
        with self.connection() as conn:
            with conn.cursor() as cur:
                return cur.executemany(sql, seq)

    @contextmanager
    def transaction(self):
        """显式事务：with db.transaction() as cur: ... 同一事务内多条写操作"""
        conn = self._pool.get(timeout=10)
        try:
            conn.ping(reconnect=True)
            cur = conn.cursor()
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.put(conn)


db = Database()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录后再访问！"
login_manager.login_message_category = "error"


@login_manager.user_loader
def load_user(user_account):
    from app.models.user_dao import UserDAO

    row = UserDAO.get_by_account(user_account)
    if row and row["status"] == "active":
        from app.models.user_dao import User

        return User(row)
    return None
