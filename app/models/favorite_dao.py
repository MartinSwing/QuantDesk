# -*- coding: utf-8 -*-
"""自选股 DAO"""
from app.extensions import db


class WatchlistDAO:
    @staticmethod
    def list_by_user(user_account: str):
        return db.fetch_all(
            "SELECT stock_code, stock_name, created_at FROM watchlist "
            "WHERE user_account = %s ORDER BY created_at DESC",
            (user_account,),
        )

    @staticmethod
    def add(user_account: str, stock_code: str, stock_name: str) -> bool:
        affected = db.execute(
            "INSERT IGNORE INTO watchlist (user_account, stock_code, stock_name) VALUES (%s,%s,%s)",
            (user_account, stock_code, stock_name),
        )
        return affected > 0

    @staticmethod
    def remove(user_account: str, stock_code: str) -> bool:
        affected = db.execute(
            "DELETE FROM watchlist WHERE user_account = %s AND stock_code = %s",
            (user_account, stock_code),
        )
        return affected > 0
