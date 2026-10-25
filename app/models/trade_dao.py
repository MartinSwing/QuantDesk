# -*- coding: utf-8 -*-
"""持仓与交易记录 DAO"""
import uuid
from datetime import datetime

from app.extensions import db


class PositionDAO:
    @staticmethod
    def list_by_user(user_account: str):
        return db.fetch_all(
            "SELECT * FROM positions WHERE user_account = %s AND quantity > 0 ORDER BY updated_at DESC",
            (user_account,),
        )

    @staticmethod
    def get(user_account: str, stock_code: str):
        return db.fetch_one(
            "SELECT * FROM positions WHERE user_account = %s AND stock_code = %s",
            (user_account, stock_code),
        )

    @staticmethod
    def upsert_after_buy(cur, user_account: str, stock_code: str, stock_name: str,
                         quantity: int, price: float, t_plus_1: bool):
        """买入后更新持仓（摊薄成本），须在外部事务中调用"""
        cur.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_account=%s AND stock_code=%s FOR UPDATE",
            (user_account, stock_code),
        )
        row = cur.fetchone()
        if row and row["quantity"] > 0:
            new_qty = row["quantity"] + quantity
            new_cost = (row["quantity"] * float(row["avg_cost"]) + quantity * price) / new_qty
            cur.execute(
                """UPDATE positions SET stock_name=%s, quantity=%s, avg_cost=%s,
                   available_quantity = available_quantity + %s
                   WHERE user_account=%s AND stock_code=%s""",
                (stock_name, new_qty, round(new_cost, 3),
                 0 if t_plus_1 else quantity, user_account, stock_code),
            )
        else:
            cur.execute(
                """INSERT INTO positions
                   (user_account, stock_code, stock_name, quantity, available_quantity, avg_cost)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE quantity=VALUES(quantity),
                     available_quantity=VALUES(available_quantity), avg_cost=VALUES(avg_cost)""",
                (user_account, stock_code, stock_name, quantity,
                 0 if t_plus_1 else quantity, round(price, 3)),
            )

    @staticmethod
    def reduce_after_sell(cur, user_account: str, stock_code: str, quantity: int):
        """卖出后扣减持仓，须在外部事务中调用"""
        cur.execute(
            "UPDATE positions SET quantity = quantity - %s, available_quantity = available_quantity - %s "
            "WHERE user_account=%s AND stock_code=%s",
            (quantity, quantity, user_account, stock_code),
        )


class OrderDAO:
    @staticmethod
    def gen_order_no(prefix: str = "TRD") -> str:
        return f"{prefix}{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def insert(cur, data: dict):
        """插入订单；data 可含 created_at（自定义成交时间，datetime）"""
        cols = ["order_no", "user_account", "stock_code", "stock_name", "side",
                "price", "quantity", "amount", "fee", "tax", "status", "remark"]
        if data.get("created_at") is not None:
            cols.append("created_at")
        placeholders = ", ".join(f"%({c})s" for c in cols)
        cur.execute(
            f"INSERT INTO orders ({', '.join(cols)}) VALUES ({placeholders})",
            data,
        )

    @staticmethod
    def list_by_user(user_account: str, limit: int = 200):
        return db.fetch_all(
            "SELECT * FROM orders WHERE user_account = %s ORDER BY created_at DESC LIMIT %s",
            (user_account, limit),
        )

    @staticmethod
    def count_by_user(user_account: str) -> int:
        rows = db.fetch_all(
            "SELECT COUNT(*) AS n FROM orders WHERE user_account = %s",
            (user_account,),
        )
        return int(rows[0]["n"]) if rows else 0

    @staticmethod
    def list_by_user_paged(user_account: str, page: int = 1, page_size: int = 20):
        """成交记录服务端分页：LIMIT/OFFSET 下推到 SQL"""
        offset = (max(1, page) - 1) * page_size
        return db.fetch_all(
            "SELECT * FROM orders WHERE user_account = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (user_account, page_size, offset),
        )


class CapitalFlowDAO:
    @staticmethod
    def gen_flow_no() -> str:
        return f"CAP{datetime.now():%Y%m%d%H%M%S}{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def insert(cur, data: dict):
        cur.execute(
            """INSERT INTO capital_flows (flow_no, user_account, flow_type, amount, status, channel, remark)
               VALUES (%(flow_no)s, %(user_account)s, %(flow_type)s, %(amount)s, %(status)s,
                       %(channel)s, %(remark)s)""",
            data,
        )

    @staticmethod
    def list_by_user(user_account: str, limit: int = 100):
        return db.fetch_all(
            "SELECT * FROM capital_flows WHERE user_account = %s ORDER BY created_at DESC LIMIT %s",
            (user_account, limit),
        )
