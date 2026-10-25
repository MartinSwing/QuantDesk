# -*- coding: utf-8 -*-
"""资金账户 DAO"""
from app.extensions import db


class AccountDAO:
    @staticmethod
    def get(user_account: str):
        return db.fetch_one("SELECT * FROM accounts WHERE user_account = %s", (user_account,))

    @staticmethod
    def ensure(user_account: str):
        db.execute("INSERT IGNORE INTO accounts (user_account) VALUES (%s)", (user_account,))

    @staticmethod
    def add_balance(user_account: str, delta: float, cur=None):
        """调整可用资金（正入负出），须在事务中或单独调用"""
        sql = ("UPDATE accounts SET available_balance = available_balance + %s "
               "WHERE user_account = %s")
        if cur is not None:
            cur.execute(sql, (delta, user_account))
        else:
            db.execute(sql, (delta, user_account))
