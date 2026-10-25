# -*- coding: utf-8 -*-
"""
技术指标计算服务（通达信口径，pandas 实现）
- MACD：EMA12/EMA26 → DIF；DEA=EMA(DIF,9)；柱=(DIF-DEA)*2
- KDJ：9日 RSV=(C-LLV9)/(HHV9-LLV9)*100；K=SMA(RSV,3,1)；D=SMA(K,3,1)；J=3K-2D
       （SMA 为中文行情软件的递归平滑：Y=(M*X+(N-M)*Y')/N）
- RSI：6/12/24 期，SMA 平滑（同上口径）
- BOLL：MA20 ± 2×STD20 → 中/上/下轨
- MA：5/10/20/60 均线族（供K线主图叠加与回测复用）
- 输入：K 线列表（MarketService.get_stock_kline / get_index_kline 输出格式）
- 输出：与 K 线逐日对齐的序列，前 N-1 日为 None（前端跳过渲染）
- 内存缓存 5 分钟（key: kind+code+days）
"""
import numpy as np
import pandas as pd

from app.utils.cache import cached

_TTL = 300


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _sma_cn(s: pd.Series, n: int, m: int) -> pd.Series:
    """通达信 SMA(X,N,M)：Y=(M*X+(N-M)*Y')/N，递归平滑"""
    out = np.zeros(len(s))
    prev = np.nan
    for i, x in enumerate(s.fillna(0).to_numpy()):
        prev = (m * x + (n - m) * prev) / n if not np.isnan(prev) else x
        out[i] = prev
    return pd.Series(out, index=s.index)


def _rsv(close: pd.Series, n: int = 9) -> pd.Series:
    low_n = close.rolling(n, min_periods=1).min()
    high_n = close.rolling(n, min_periods=1).max()
    rng = (high_n - low_n).replace(0, np.nan)
    return ((close - low_n) / rng * 100).fillna(50.0)


def _rsi(close: pd.Series, n: int) -> pd.Series:
    """通达信 RSI：SMA(MAX(diff,0),N,1) / SMA(ABS(diff),N,1) * 100"""
    diff = close.diff().fillna(0.0)
    up = _sma_cn(diff.clip(lower=0), n, 1)
    total = _sma_cn(diff.abs(), n, 1)
    return (up / total.replace(0, np.nan) * 100).fillna(50.0)


def compute_indicators(kline: list) -> dict:
    """输入 K 线列表 → 四组指标序列（None 对齐前 N-1 日）"""
    if not kline:
        return {}
    df = pd.DataFrame(kline)
    close = df["close"].astype(float)

    # MACD
    ema12, ema26 = _ema(close, 12), _ema(close, 26)
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2

    # KDJ
    rsv = _rsv(close, 9)
    k = _sma_cn(rsv, 3, 1)
    d = _sma_cn(k, 3, 1)
    j = 3 * k - 2 * d

    # RSI
    r6, r12, r24 = _rsi(close, 6), _rsi(close, 12), _rsi(close, 24)

    # BOLL
    mid = close.rolling(20, min_periods=1).mean()
    std = close.rolling(20, min_periods=1).std(ddof=0).fillna(0.0)
    boll_up, boll_low = mid + 2 * std, mid - 2 * std

    # MA 均线族
    ma5 = close.rolling(5, min_periods=1).mean()
    ma10 = close.rolling(10, min_periods=1).mean()
    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()

    def seq(s, nd=2):
        return [None if pd.isna(v) else round(float(v), nd) for v in s]

    return {
        "macd": {"dif": seq(dif), "dea": seq(dea), "hist": seq(hist)},
        "kdj": {"k": seq(k), "d": seq(d), "j": seq(j, 2)},
        "rsi": {"r6": seq(r6), "r12": seq(r12), "r24": seq(r24)},
        "boll": {"mid": seq(mid), "up": seq(boll_up), "low": seq(boll_low)},
        "ma": {"ma5": seq(ma5), "ma10": seq(ma10), "ma20": seq(ma20), "ma60": seq(ma60)},
    }


class IndicatorService:

    @staticmethod
    def get(code: str, kind: str, days: int,
            start_date=None, end_date=None) -> dict:
        """kind: stock | index；返回 {kline, indicators}
        传 start_date/end_date（YYYY-MM-DD）时按区间取数并参与缓存 key"""
        from app.services.market_service import MarketService

        if start_date and end_date:
            cache_key = (kind, code, f"{start_date}~{end_date}")
        else:
            cache_key = (kind, code, days)

        def _calc():
            kline = (MarketService.get_stock_kline(code, days, start_date, end_date)
                     if kind == "stock"
                     else MarketService.get_index_kline(code, days))
            if not kline:
                return {}
            return {"kline": kline, "indicators": compute_indicators(kline)}

        return cached(cache_key, _TTL, _calc)
