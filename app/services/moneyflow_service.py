# -*- coding: utf-8 -*-
"""
资金流向服务：个股主力资金 / 北向资金（沪深港通）
- 个股：主力 = 超大单 + 大单；含中单/小单，逐日净流入（万元）
- 北向：沪股通 + 深股通 净买入（moneyflow_hsgt）
- 内存缓存 5 分钟
"""
import logging
from datetime import datetime, timedelta

import pandas as pd

from app.datasources.tushare_client import ts_client
from app.utils.cache import cached

logger = logging.getLogger(__name__)
_TTL = 300


def get_stock_moneyflow(code: str, days: int = 60) -> dict:
    """个股逐日资金流：{dates, main, elg, lg, md, sm, total} 单位万元"""
    def _calc():
        end = datetime.now()
        start = end - timedelta(days=days)
        try:
            df = ts_client.call(
                "moneyflow", ts_code=code,
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            logger.warning("个股资金流拉取失败: %s", e)
            return {}
        if df is None or df.empty:
            return {}
        df = df.sort_values("trade_date")
        dates = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in df["trade_date"].astype(str)]
        # 净流入 = 买入额 - 卖出额（万元）
        elg = df["buy_elg_amount"] - df["sell_elg_amount"]
        lg = df["buy_lg_amount"] - df["sell_lg_amount"]
        md = df["buy_md_amount"] - df["sell_md_amount"]
        sm = df["buy_sm_amount"] - df["sell_sm_amount"]
        main = elg + lg                      # 主力 = 超大单 + 大单

        def s(v):
            return [round(float(x), 2) for x in v]

        return {
            "dates": dates,
            "main": s(main),
            "elg": s(elg),
            "lg": s(lg),
            "md": s(md),
            "sm": s(sm),
            "total": s(df["net_mf_amount"]),
        }

    return cached(("moneyflow", code, days), _TTL, _calc)


def get_north_flow(days: int = 60) -> dict:
    """北向资金逐日净买入：{dates, north, hgt, sgt} 单位万元"""
    def _calc():
        end = datetime.now()
        start = end - timedelta(days=days)
        try:
            df = ts_client.call(
                "moneyflow_hsgt",
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            logger.warning("北向资金拉取失败: %s", e)
            return {}
        if df is None or df.empty:
            return {}
        df = df.sort_values("trade_date")
        dates = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in df["trade_date"].astype(str)]
        return {
            "dates": dates,
            "north": [round(float(v), 2) for v in df["north_money"]],
            "hgt": [round(float(v), 2) for v in df["hgt"]],
            "sgt": [round(float(v), 2) for v in df["sgt"]],
        }

    return cached(("north_flow", days), _TTL, _calc)
