# -*- coding: utf-8 -*-
"""
港股行情服务（akshare 数据源，免费不限频）
- 港股列表 + 实时行情：ak.stock_hk_spot()
- 港股日K：ak.stock_hk_daily(symbol)
- 内存缓存 5 分钟
"""
import logging

import akshare as ak
import pandas as pd

from app.utils.cache import cached

logger = logging.getLogger(__name__)
_TTL = 300


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def get_hk_list() -> list:
    """港股实时行情列表（恒指/热门港股）"""
    def _calc():
        try:
            df = ak.stock_hk_spot()
        except Exception as e:
            logger.warning("港股行情拉取失败: %s", e)
            return []
        if df is None or df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            code = str(r.get("symbol", "")).zfill(5)
            price = _f(r.get("lasttrade"))
            prev = _f(r.get("prevclose"))
            pct = round((price / prev - 1) * 100, 2) if price and prev else None
            rows.append({
                "code": code,
                "name": str(r.get("name", "")),
                "price": price,
                "pct": pct,
                "open": _f(r.get("open")),
                "high": _f(r.get("high")),
                "low": _f(r.get("low")),
            })
        rows.sort(key=lambda x: x["pct"] if x["pct"] is not None else -999, reverse=True)
        return rows

    return cached(("hk_list",), _TTL, _calc)


def get_hk_kline(symbol: str, days: int = 250) -> list:
    """港股日K（akshare，symbol 为 5 位代码如 00700）"""
    def _calc():
        try:
            df = ak.stock_hk_daily(symbol=symbol, adjust="")
        except Exception as e:
            logger.warning("港股日线拉取失败 %s: %s", symbol, e)
            return []
        if df is None or df.empty:
            return []
        df = df.tail(days)
        kline = []
        for _, r in df.iterrows():
            kline.append({
                "date": str(r.get("date")),
                "open": _f(r.get("open")),
                "high": _f(r.get("high")),
                "low": _f(r.get("low")),
                "close": _f(r.get("close")),
                "volume": _f(r.get("volume")),
            })
        return kline

    return cached(("hk_kline", symbol, days), _TTL, _calc)
