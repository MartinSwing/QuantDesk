# -*- coding: utf-8 -*-
"""
交易日回溯工具：统一此前 heatmap/screener/market_hot 三处各自复制的
"目标日无数据则向前回溯 N 天" 循环
"""
from datetime import datetime, timedelta


def _truthy(data) -> bool:
    """DataFrame/列表非空、其他非 None 即视为有效"""
    if data is None:
        return False
    try:
        return len(data) > 0
    except TypeError:
        return True


def backtrack(fetch, start_date=None, lookback=6, date_fmt="%Y%m%d"):
    """
    从 start_date（缺省取当前目标交易日）向前最多回溯 lookback 个自然日，
    逐日调用 fetch(date_str)，首个非空结果以 (data, date_str) 返回；
    全部为空返回 (None, "")。fetch 异常视为该日无数据（由调用方日志）。
    """
    if start_date is None:
        from app.datasources.tushare_client import get_target_trade_date
        try:
            start_date = get_target_trade_date()
        except Exception:
            start_date = datetime.now().strftime("%Y%m%d")
    day = datetime.strptime(start_date, "%Y%m%d")
    for _ in range(max(1, lookback)):
        ds = day.strftime(date_fmt)
        try:
            data = fetch(ds)
        except Exception:
            data = None
        if _truthy(data):
            return data, ds
        day -= timedelta(days=1)
    return None, ""
