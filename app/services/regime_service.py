# -*- coding: utf-8 -*-
"""
市场状态识别服务：基于指数均线排列 + 波动率分位做三分类打标
- 趋势上行（牛市）：收盘 > MA20 > MA60 且 20 日涨幅为正
- 趋势下行（熊市）：收盘 < MA20 < MA60 且 20 日涨幅为负
- 震荡市：均线缠绕，其余情形
- 波动率等级：近一年 20 日年化波动率的分位数（>0.7 高波动 / <0.3 低波动）
- 输出适配策略建议（趋势市→趋势跟踪类；震荡市→均值回归类）
- 结果缓存 10 分钟
"""
import logging
import math
from datetime import date, timedelta

import pandas as pd

from app.datasources.local_cache import get_index_kline, upsert_index_kline
from app.datasources.tushare_client import ts_client
from app.utils.cache import cached

logger = logging.getLogger(__name__)

_TTL = 600
DEFAULT_INDEX = "000300.SH"   # 沪深300
LOOKBACK_DAYS = 400           # 取约 400 自然日（~270 交易日），够 MA60 + 波动率分位

# 状态 → (适配策略 key 列表, 建议描述)
REGIME_STRATEGIES = {
    "uptrend": (["ma_cross", "macd", "donchian", "momentum"],
                "趋势上行：趋势跟踪类策略（双均线 / MACD / 唐奇安 / 动量）更契合"),
    "downtrend": ([],
                  "趋势下行：建议观望或轻仓，趋势类策略应空仓等待"),
    "sideways": (["boll_revert", "kdj", "grid"],
                 "震荡市：均值回归类策略（布林带 / KDJ / 网格）更契合"),
}


def _load_index(code: str) -> list:
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    rows = get_index_kline(code, start, end)
    if len(rows) < 60:  # 本地覆盖不足 → Tushare 补拉落库
        try:
            df = ts_client.call(
                "index_daily", ts_code=code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                fields="trade_date,open,high,low,close,pre_close,vol,amount",
            )
            if df is not None and not df.empty:
                upsert_index_kline(code, df)
                rows = get_index_kline(code, start, end)
        except Exception:
            logger.warning("市场状态取数补拉失败，用本地缓存兜底")
    return rows


def detect(index_code: str = DEFAULT_INDEX) -> dict:
    """识别当前市场状态，返回 {regime, metrics, recommend, ...}"""

    def _calc():
        rows = _load_index(index_code)
        if len(rows) < 60:
            return {"error": "指数历史数据不足，暂无法识别市场状态"}
        close = pd.Series({str(r["trade_date"]): float(r["close"])
                           for r in rows}).sort_index()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        ret = close.pct_change()
        vol = ret.rolling(20).std() * math.sqrt(252)   # 20 日年化波动率

        c = float(close.iloc[-1])
        m20, m60 = float(ma20.iloc[-1]), float(ma60.iloc[-1])
        ret20 = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 else 0.0
        vol_now = float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else 0.0
        vol_hist = vol.dropna()
        vol_pct = float((vol_hist <= vol_now).mean()) if vol_now and len(vol_hist) else 0.0

        # 三分类
        if c > m20 > m60 and ret20 > 0:
            regime, regime_en = "趋势上行", "uptrend"
        elif c < m20 < m60 and ret20 < 0:
            regime, regime_en = "趋势下行", "downtrend"
        else:
            regime, regime_en = "震荡市", "sideways"

        if vol_pct >= 0.7:
            vol_level = "高波动"
        elif vol_pct <= 0.3:
            vol_level = "低波动"
        else:
            vol_level = "中波动"

        recommend, desc = REGIME_STRATEGIES[regime_en]
        return {
            "date": close.index[-1],
            "index_code": index_code,
            "regime": regime,
            "regime_en": regime_en,
            "volatility_level": vol_level,
            "metrics": {
                "close": round(c, 2),
                "ma20": round(m20, 2),
                "ma60": round(m60, 2),
                "ret_20d_pct": round(ret20 * 100, 2),
                "volatility_annual_pct": round(vol_now * 100, 2),
                "volatility_percentile": round(vol_pct * 100, 0),
            },
            "recommend": recommend,
            "recommend_desc": desc,
        }

    return cached(("regime", index_code), _TTL, _calc)
