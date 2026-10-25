# -*- coding: utf-8 -*-
"""
价值投资指标历史服务：PE(TTM) / PB / 股息率 / 总市值
- 数据源：Tushare daily_basic（600 积分可用）
- 内存缓存 1 小时（历史估值序列变化低频）
- 附当前值与历史分位（当前值在区间内的相对位置，价值投资核心参考）
"""
import logging
from datetime import datetime, timedelta

import pandas as pd

from app.datasources.tushare_client import ts_client
from app.utils.cache import cached

logger = logging.getLogger(__name__)

_TTL = 3600
_FIELDS = "trade_date,pe_ttm,pb,dv_ratio,total_mv"


def _pct_rank(vals: list, cur) -> float:
    """当前值在历史序列中的分位（0~100，越大越贵）"""
    clean = [v for v in vals if v is not None]
    if not clean or cur is None:
        return None
    return round(sum(1 for v in clean if v <= cur) / len(clean) * 100, 1)


def get_history(code: str, years: int = 2) -> dict:
    """返回 {dates, series:{pe,pb,dv_ratio,total_mv}, current, percentile}"""
    def _calc():
        end = datetime.now()
        start = end - timedelta(days=int(365 * years))
        try:
            df = ts_client.call(
                "daily_basic", ts_code=code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields=_FIELDS,
            )
        except Exception as e:
            logger.warning("估值数据拉取失败: %s", e)
            return {}
        if df is None or df.empty:
            return {}
        df = df.sort_values("trade_date")
        dates = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in df["trade_date"].astype(str)]

        def seq(col, scale=1.0):
            return [None if pd.isna(v) else round(float(v) * scale, 4)
                    for v in df[col]]

        pe = seq("pe_ttm")
        pb = seq("pb")
        dv = seq("dv_ratio")
        mv = seq("total_mv", 1 / 10000.0)   # 万元 → 亿元

        def cur(s):
            return next((v for v in reversed(s) if v is not None), None)

        current = {"pe": cur(pe), "pb": cur(pb),
                   "dv_ratio": cur(dv), "total_mv": cur(mv)}
        percentile = {k: _pct_rank(s, current[k])
                      for k, s in (("pe", pe), ("pb", pb),
                                   ("dv_ratio", dv), ("total_mv", mv))}
        return {"dates": dates,
                "series": {"pe": pe, "pb": pb, "dv_ratio": dv, "total_mv": mv},
                "current": current, "percentile": percentile,
                "count": len(dates)}

    return cached(("valuation", code, years), _TTL, _calc)
