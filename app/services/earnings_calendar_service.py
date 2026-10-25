# -*- coding: utf-8 -*-
"""
财报日历服务：财报披露日历 + 业绩快报（营收/利润同比）
- 财报披露日历：disclosure_date（未来披露计划）
- 业绩快报：express（最新报告期，营收/利润及同比增速）
- 内存缓存 30 分钟
"""
import logging
from datetime import datetime, timedelta

import pandas as pd

from app.datasources.tushare_client import ts_client
from app.utils.cache import cached

logger = logging.getLogger(__name__)
_TTL = 1800


def _latest_period() -> str:
    """最新已结束报告期（YYYYMMDD）"""
    now = datetime.now()
    y, m = now.year, now.month
    if m <= 3:
        return f"{y - 1}1231"
    if m <= 6:
        return f"{y}0331"
    if m <= 9:
        return f"{y}0630"
    return f"{y}0930"


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _fmt_date(s):
    s = str(s)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else s


def get_calendar() -> dict:
    """返回 {disclosures:[...], expresses:[...], period}"""
    def _calc():
        today = datetime.now()
        # ① 财报披露日历（未来 60 天预约披露）
        disclosures = []
        try:
            disc = ts_client.call(
                "disclosure_date",
                start_date=today.strftime("%Y%m%d"),
                end_date=(today + timedelta(days=60)).strftime("%Y%m%d"),
            )
            if disc is not None and not disc.empty:
                disc = disc.sort_values("ann_date")
                for _, r in disc.head(200).iterrows():
                    disclosures.append({
                        "ts_code": r.get("ts_code"),
                        "ann_date": _fmt_date(r.get("ann_date")),
                        "end_date": _fmt_date(r.get("end_date")),
                    })
        except Exception as e:
            logger.warning("财报披露日历拉取失败: %s", e)

        # ② 业绩快报（最新报告期）
        period = _latest_period()
        expresses = []
        try:
            exp = ts_client.call("express", period=period)
            if exp is not None and not exp.empty:
                exp = exp.sort_values("ann_date", ascending=False)
                for _, r in exp.head(200).iterrows():
                    expresses.append({
                        "ts_code": r.get("ts_code"),
                        "ann_date": _fmt_date(r.get("ann_date")),
                        "revenue": round(_f(r.get("revenue")) / 1e8, 2) if _f(r.get("revenue")) is not None else None,
                        "net_profit": round(_f(r.get("net_profit")) / 1e8, 2) if _f(r.get("net_profit")) is not None else None,
                        "revenue_yoy": _f(r.get("revenue_yoy")),
                        "net_profit_yoy": _f(r.get("net_profit_yoy")),
                        "eps": _f(r.get("eps")),
                    })
        except Exception as e:
            logger.warning("业绩快报拉取失败: %s", e)

        return {"period": period, "disclosures": disclosures,
                "expresses": expresses}

    return cached(("earnings_calendar",), _TTL, _calc)
