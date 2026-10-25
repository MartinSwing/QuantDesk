# -*- coding: utf-8 -*-
"""
分时图 + 五档盘口服务（0 积分）
- 分时：akshare stock_zh_a_minute（新浪 1 分钟线，含近几个交易日）
        → 取最近交易日，构建 09:30-15:00 标准 241 分钟轴（午休对齐）
- 均价线：累计成交额加权（sum(价*量)/sum(量)）
- 昨收：新浪分钟数据里目标日之前最后一根收盘价
- 五档：tushare 旧版实时接口（新浪源，免积分）get_realtime_quotes
- 缓存：分时 60 秒 / 五档 15 秒
"""
import logging
import time

logger = logging.getLogger(__name__)

_MIN_TTL = 60      # 分时
_BID_TTL = 15      # 五档

from app.utils.cache import cached


def _sina_symbol(code: str) -> str:
    """603816.SH → sh603816"""
    c = code.split(".")[0]
    sfx = code.partition(".")[2].lower()
    if sfx in ("sh", "sz", "bj"):
        return sfx + c
    if c.startswith(("6", "9", "5")):
        return "sh" + c
    if c.startswith(("8", "4")):
        return "bj" + c
    return "sz" + c


def _session_axis():
    """标准交易分钟轴：09:30-11:30 + 13:00-15:00 共 241 个刻度"""
    axis = []
    for h, m in [(9, 30), (9, 31), (9, 32), (9, 33), (9, 34), (9, 35), (9, 36), (9, 37),
                 (9, 38), (9, 39), (9, 40), (9, 41), (9, 42), (9, 43), (9, 44), (9, 45),
                 (9, 46), (9, 47), (9, 48), (9, 49), (9, 50), (9, 51), (9, 52), (9, 53),
                 (9, 54), (9, 55), (9, 56), (9, 57), (9, 58), (9, 59)]:
        axis.append(f"{h:02d}:{m:02d}")
    axis += [f"10:{m:02d}" for m in range(0, 60)]
    axis += [f"11:{m:02d}" for m in range(0, 31)]                      # 11:30 含
    axis += [f"13:{m:02d}" for m in range(1, 60)]                      # 13:01 起
    axis += [f"14:{m:02d}" for m in range(0, 60)]
    axis += [f"15:00"]
    return axis


class MinuteService:

    # ---------- 分时 ----------
    @staticmethod
    def get_intraday(stock_code: str) -> dict:
        return cached(("intraday", stock_code), _MIN_TTL,
                      lambda: MinuteService._intraday(stock_code))

    @staticmethod
    def _intraday(stock_code: str) -> dict:
        from app.datasources.web_client import ak_call
        import akshare as ak
        try:
            df = ak_call(ak.stock_zh_a_minute, symbol=_sina_symbol(stock_code),
                         period="1", adjust="")
        except Exception as e:
            logger.warning(f"⚠️ 新浪分时不可用({stock_code}): {str(e)[:80]}")
            return {}
        if df is None or df.empty:
            return {}

        df["day"] = df["day"].astype(str)
        dates = sorted(df["day"].str[:10].unique())
        target = dates[-1]
        day_df = df[df["day"].str[:10] == target].copy()
        prev_df = df[df["day"].str[:10] < target]
        pre_close = float(prev_df.iloc[-1]["close"]) if not prev_df.empty else None

        # 标准轴映射
        axis = _session_axis()
        axis_idx = {t: i for i, t in enumerate(axis)}
        prices = [None] * len(axis)
        avgs = [None] * len(axis)
        cum_pv = cum_v = 0.0
        for r in day_df.itertuples(index=False):
            t = str(r.day)[11:16]
            i = axis_idx.get(t)
            if i is None:
                continue
            price = float(r.close)
            vol = float(r.volume or 0)
            prices[i] = price
            cum_pv += price * vol
            cum_v += vol
            avgs[i] = round(cum_pv / cum_v, 3) if cum_v else price

        # ECharts 分时常用：从首个有值处开始展示，压缩前导空档
        first = next((i for i, p in enumerate(prices) if p is not None), 0)
        close = next((p for p in reversed(prices) if p is not None), None)
        valid = [p for p in prices if p is not None]
        return {
            "date": target,
            "pre_close": pre_close,
            "axis": axis[first:],
            "price": prices[first:],
            "avg": avgs[first:],
            "high": max(valid) if valid else None,
            "low": min(valid) if valid else None,
            "close": close,
            "source": "新浪财经(1分钟线)",
        }

    # ---------- 五档盘口 ----------
    @staticmethod
    def get_bid_ask(stock_code: str) -> dict:
        return cached(("bidask", stock_code), _BID_TTL,
                       lambda: MinuteService._bid_ask(stock_code))

    @staticmethod
    def _bid_ask(stock_code: str) -> dict:
        code6 = stock_code.split(".")[0]
        try:
            import tushare as ts
            df = ts.get_realtime_quotes(code6)
        except Exception as e:
            logger.warning(f"⚠️ 实时五档不可用({stock_code}): {str(e)[:80]}")
            return {}
        if df is None or df.empty:
            return {}
        r = df.iloc[0]

        def f(v):
            try:
                v = float(v)
                return v if v == v else None
            except (TypeError, ValueError):
                return None

        bids = [{"p": f(r[f"b{i}_p"]), "v": f(r[f"b{i}_v"])} for i in range(1, 6)]
        asks = [{"p": f(r[f"a{i}_p"]), "v": f(r[f"a{i}_v"])} for i in range(1, 6)]
        return {
            "price": f(r["price"]), "pre_close": f(r["pre_close"]),
            "open": f(r["open"]), "high": f(r["high"]), "low": f(r["low"]),
            "bids": bids, "asks": asks,
            "time": str(r.get("time") or ""),
            "source": "新浪行情(tushare实时接口)",
        }
