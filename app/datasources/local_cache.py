# -*- coding: utf-8 -*-
"""本地数据库缓存读写（Tushare 不可用时的降级数据源）"""
import logging

from datetime import date

logger = logging.getLogger(__name__)

import pandas as pd

from app.extensions import db


def load_stock_basic_safe() -> pd.DataFrame:
    conn = None
    try:
        conn = _conn()
        return pd.read_sql(
            "SELECT ts_code, symbol, name, area, industry, market, list_date FROM stock_basic",
            conn,
        )
    except Exception as e:
        logger.warning(f"❌ 读取本地 stock_basic 失败: {e}")
        return pd.DataFrame()
    finally:
        if conn is not None:
            db._pool.put(conn)


def upsert_stock_basic(df: pd.DataFrame):
    rows = df.where(pd.notna(df), "").values.tolist()
    db.executemany(
        """REPLACE INTO stock_basic (ts_code, symbol, name, area, industry, market, list_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        rows,
    )


def get_latest_daily_quotes(stock_codes) -> dict:
    """按股票代码取本地 stock_daily 最新一条行情（索引支撑，毫秒级）
    返回 {ts_code: {open,high,low,close,pre_close,pct_chg,vol,amount,trade_date}}
    """
    quotes = {}
    if not stock_codes:
        return quotes
    with db.connection() as conn:
        with conn.cursor() as cur:
            for code in stock_codes:
                try:
                    cur.execute(
                        "SELECT open, high, low, close, pre_close, pct_chg, vol, amount, trade_date "
                        "FROM stock_daily WHERE ts_code = %s ORDER BY trade_date DESC LIMIT 1",
                        (code,),
                    )
                    row = cur.fetchone()
                    if row:
                        quotes[code] = row
                except Exception as e:
                    logger.warning(f"读取{code}本地行情失败: {e}")
    return quotes


def get_daily_kline(stock_code: str, start: date, end: date) -> list:
    """取本地日K缓存"""
    rows = db.fetch_all(
        "SELECT trade_date, open, high, low, close, pre_close, pct_chg, vol, amount "
        "FROM stock_daily WHERE ts_code = %s AND trade_date BETWEEN %s AND %s "
        "ORDER BY trade_date ASC",
        (stock_code, start, end),
    )
    return rows


def upsert_daily_kline(stock_code: str, df: pd.DataFrame):
    """Tushare 日线数据写入缓存（REPLACE 支持增量更新）"""
    if df is None or df.empty:
        return
    cols = ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    rows = []
    for _, r in df.iterrows():
        rows.append((
            stock_code,
            str(r["trade_date"])[:4] + "-" + str(r["trade_date"])[4:6] + "-" + str(r["trade_date"])[6:8],
            *[None if pd.isna(r.get(c)) else float(r.get(c)) for c in cols],
        ))
    db.executemany(
        """REPLACE INTO stock_daily
           (ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        rows,
    )


def get_index_kline(index_code: str, start: date, end: date) -> list:
    return db.fetch_all(
        "SELECT trade_date, open, high, low, close, pre_close, vol, amount "
        "FROM index_daily WHERE ts_code = %s AND trade_date BETWEEN %s AND %s "
        "ORDER BY trade_date ASC",
        (index_code, start, end),
    )


def upsert_index_kline(index_code: str, df: pd.DataFrame):
    if df is None or df.empty:
        return
    rows = []
    for _, r in df.iterrows():
        rows.append((
            index_code,
            str(r["trade_date"])[:4] + "-" + str(r["trade_date"])[4:6] + "-" + str(r["trade_date"])[6:8],
            None if pd.isna(r.get("open")) else float(r["open"]),
            None if pd.isna(r.get("high")) else float(r["high"]),
            None if pd.isna(r.get("low")) else float(r["low"]),
            None if pd.isna(r.get("close")) else float(r["close"]),
            None if pd.isna(r.get("pre_close")) else float(r["pre_close"]),
            None if pd.isna(r.get("vol")) else float(r.get("vol", 0)),
            None if pd.isna(r.get("amount")) else float(r.get("amount", 0)),
        ))
    db.executemany(
        """REPLACE INTO index_daily
           (ts_code, trade_date, open, high, low, close, pre_close, vol, amount)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        rows,
    )
