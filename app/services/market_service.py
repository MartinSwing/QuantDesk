# -*- coding: utf-8 -*-
"""
行情服务：组合 Tushare 在线数据与本地缓存
策略：优先在线获取 → 成功后写本地缓存 → 失败/冷却期自动降级本地缓存
"""
import logging

import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

import pandas as pd

from app.datasources import local_cache
from app.datasources.tushare_client import get_target_trade_date, ts_client
from app.extensions import db
from config import settings

CORE_INDEXES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000016.SH": "上证50",
    "000300.SH": "沪深300",
    "000688.SH": "科创50",
}


class MarketService:
    _basic_lock = threading.Lock()
    _basic_cache = None
    _basic_time = 0

    # ---------- 股票基础信息 ----------
    @staticmethod
    def get_stock_basic() -> pd.DataFrame:
        """股票基础信息：内存缓存(1h) → Tushare → 本地表"""
        now = time.time()
        ttl = settings.MARKET_RULES["basic_cache_seconds"]
        if MarketService._basic_cache is not None and now - MarketService._basic_time < ttl:
            return MarketService._basic_cache
        with MarketService._basic_lock:
            # 双重检查
            now = time.time()
            if MarketService._basic_cache is not None and now - MarketService._basic_time < ttl:
                return MarketService._basic_cache
            try:
                df = ts_client.call(
                    "stock_basic", list_status="L",
                    fields="ts_code,symbol,name,area,industry,market,list_date",
                )
                if df is not None and not df.empty:
                    MarketService._basic_cache = df
                    MarketService._basic_time = time.time()
                    local_cache.upsert_stock_basic(df)
                    return df
            except Exception as e:
                logger.warning(f"⚠️ Tushare基础信息不可用，降级本地缓存: {e}")
            df = local_cache.load_stock_basic_safe()
            if not df.empty:
                MarketService._basic_cache = df
                MarketService._basic_time = time.time()
            return df

    @staticmethod
    def search_stocks(keyword: str, limit: int = 10) -> list:
        df = MarketService.get_stock_basic()
        if df is None or df.empty:
            return []
        mask = (
            df["ts_code"].str.contains(keyword, na=False)
            | df["symbol"].str.contains(keyword, na=False)
            | df["name"].str.contains(keyword, na=False)
        )
        picked = df[mask].head(limit)
        return picked.to_dict("records")

    @staticmethod
    def get_stock_name(stock_code: str) -> str:
        df = MarketService.get_stock_basic()
        if df is None or df.empty:
            return stock_code
        row = df[df["ts_code"] == stock_code]
        return row.iloc[0]["name"] if not row.empty else stock_code

    # ---------- 实时行情 ----------
    @staticmethod
    def get_realtime_quotes(stock_codes) -> list:
        """批量行情。在线成功返回最新数据；失败降级本地 stock_daily 最新一条"""
        if not stock_codes:
            return []
        degraded = False
        try:
            target = get_target_trade_date()
            daily = ts_client.call(
                "daily", ts_code=",".join(stock_codes),
                start_date=target, end_date=target,
                fields="ts_code,trade_date,close,pre_close,vol,amount,pct_chg,high,low",
            )
        except Exception:
            degraded = True

        if degraded or daily is None or daily.empty:
            # 目标日无数据时扩最近7天再试一次
            if not degraded:
                try:
                    start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
                    daily = ts_client.call(
                        "daily", ts_code=",".join(stock_codes),
                        start_date=start, end_date=get_target_trade_date(),
                        fields="ts_code,trade_date,close,pre_close,vol,amount,pct_chg,high,low",
                    )
                except Exception:
                    degraded = True
            if degraded or daily is None or daily.empty:
                return MarketService._quotes_from_local(stock_codes)

        basic_df = MarketService.get_stock_basic()
        result = []
        for code in stock_codes:
            row_df = daily[daily["ts_code"] == code].sort_values("trade_date")
            name = MarketService._name_from(basic_df, code)
            if row_df.empty:
                result.append(MarketService._empty_quote(code, name))
                continue
            r = row_df.iloc[-1]
            result.append({
                "stock_code": code,
                "stock_name": name,
                "trade_date": str(r["trade_date"]),
                "current_price": round(float(r["close"]), 2),
                "pct_change": round(float(r["pct_chg"]), 2) if pd.notna(r["pct_chg"]) else 0.0,
                "volume": round(float(r["vol"]) / 10000, 2),   # 万手
                "amount": round(float(r["amount"]) / 100000, 2),  # 亿元(千元→亿)
                "high": round(float(r["high"]), 2),
                "low": round(float(r["low"]), 2),
                "source": "tushare",
            })
        return result

    @staticmethod
    def _name_from(basic_df, code):
        if basic_df is None or basic_df.empty:
            return code
        row = basic_df[basic_df["ts_code"] == code]
        return row.iloc[0]["name"] if not row.empty else code

    @staticmethod
    def _empty_quote(code, name):
        return {"stock_code": code, "stock_name": name, "trade_date": "",
                "current_price": None, "pct_change": 0.0, "volume": None,
                "amount": None, "high": None, "low": None, "source": "none"}

    @staticmethod
    def _quotes_from_local(stock_codes) -> list:
        quotes = local_cache.get_latest_daily_quotes(stock_codes)
        basic_df = MarketService.get_stock_basic()
        result = []
        for code in stock_codes:
            q = quotes.get(code)
            name = MarketService._name_from(basic_df, code)
            if not q:
                result.append(MarketService._empty_quote(code, name))
                continue
            result.append({
                "stock_code": code,
                "stock_name": name,
                "trade_date": str(q["trade_date"]),
                "current_price": round(float(q["close"]), 2) if q["close"] else None,
                "pct_change": round(float(q["pct_chg"]), 2) if q["pct_chg"] else 0.0,
                "volume": round(float(q["vol"]) / 10000, 2) if q["vol"] else None,
                "amount": round(float(q["amount"]) / 100000, 2) if q["amount"] else None,
                "high": round(float(q["high"]), 2) if q["high"] else None,
                "low": round(float(q["low"]), 2) if q["low"] else None,
                "source": "local_cache",
            })
        return result

    @staticmethod
    def get_latest_price(stock_code: str):
        """供交易使用的最新价；返回 None 表示暂无行情"""
        quotes = MarketService.get_realtime_quotes([stock_code])
        q = quotes[0] if quotes else None
        return q["current_price"] if q and q["current_price"] else None

    # ---------- K线 ----------
    @staticmethod
    def get_stock_kline(stock_code: str, days: int = 120,
                        start_date=None, end_date=None) -> list:
        """days 语义：最近 N 天；也可传 start_date/end_date 取指定区间（datetime/date/字符串均可）"""
        def _as_dt(v):
            if isinstance(v, datetime):
                return v
            if hasattr(v, "year"):        # date
                return datetime(v.year, v.month, v.day)
            return datetime.strptime(str(v), "%Y-%m-%d")

        if end_date is not None:
            end = _as_dt(end_date) + timedelta(days=1)   # 含 end 当日
        else:
            end = datetime.now()
        if start_date is not None:
            start = _as_dt(start_date)
        else:
            start = end - timedelta(days=days)
        kline = []
        try:
            df = ts_client.call(
                "daily", ts_code=stock_code,
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
                fields="trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
            )
            if df is not None and not df.empty:
                local_cache.upsert_daily_kline(stock_code, df)
                df = df.sort_values("trade_date")
                kline = df.to_dict("records")
        except Exception as e:
            logger.warning(f"⚠️ 在线K线不可用，降级本地: {e}")
        if not kline:
            rows = local_cache.get_daily_kline(stock_code, start.date(), end.date())
            kline = [{**r, "trade_date": str(r["trade_date"]).replace("-", "")} for r in rows]
        return [MarketService._fmt_kline(r, is_index=False) for r in kline]

    @staticmethod
    def get_index_kline(index_code: str, days: int = 120) -> list:
        end = datetime.now()
        start = end - timedelta(days=days)
        kline = []
        try:
            df = ts_client.call(
                "index_daily", ts_code=index_code,
                start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
                fields="trade_date,open,high,low,close,pre_close,vol,amount",
            )
            if df is not None and not df.empty:
                local_cache.upsert_index_kline(index_code, df)
                df = df.sort_values("trade_date")
                kline = df.to_dict("records")
        except Exception as e:
            logger.warning(f"⚠️ 在线指数K线不可用，降级本地: {e}")
        if not kline:
            rows = local_cache.get_index_kline(index_code, start.date(), end.date())
            kline = [{**r, "trade_date": str(r["trade_date"]).replace("-", "")} for r in rows]
        return [MarketService._fmt_kline(r, is_index=True) for r in kline]

    @staticmethod
    def _fmt_kline(r: dict, is_index: bool) -> dict:
        td = str(r["trade_date"])
        date_str = f"{td[:4]}-{td[4:6]}-{td[6:8]}" if "-" not in td else td
        pre = float(r.get("pre_close") or 0)
        close = float(r.get("close") or 0)
        change = round(close - pre, 2) if pre else 0
        pct = round(change / pre * 100, 2) if pre else 0
        out = {
            "date": date_str,
            "open": round(float(r.get("open") or 0), 2),
            "high": round(float(r.get("high") or 0), 2),
            "low": round(float(r.get("low") or 0), 2),
            "close": round(close, 2),
            "change": change,
            "pct_change": pct,
        }
        if not is_index:
            out["volume"] = round(float(r.get("vol") or 0) / 10000, 2)
            out["amount"] = round(float(r.get("amount") or 0) / 100000, 2)
        else:
            # 指数：vol 单位为手 → 亿手；amount 实测为千元口径（文档标注亿元有误）→ 亿元
            out["volume"] = round(float(r.get("vol") or 0) / 1e8, 2)
            out["amount"] = round(float(r.get("amount") or 0) / 100000, 2)
        return out

    # ---------- 指数概览 ----------
    @staticmethod
    def get_index_overview() -> list:
        """逐个指数查询（index_daily 不支持多代码批量），任一成功即计入"""
        codes = list(CORE_INDEXES.keys())
        result = []
        end_ts = datetime.now().strftime("%Y%m%d")
        start_ts = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        for code in codes:
            df = None
            try:
                df = ts_client.call(
                    "index_daily", ts_code=code,
                    start_date=start_ts, end_date=end_ts,
                    fields="ts_code,trade_date,close,pre_close,vol,amount",
                )
            except Exception:
                df = None
            if df is None or df.empty:
                # 本地降级
                rows = local_cache.get_index_kline(
                    code, datetime(2000, 1, 1).date(), datetime.now().date())
                if rows:
                    r = rows[-1]
                else:
                    continue
                pre = float(r["pre_close"] or 0)
                close = float(r["close"] or 0)
                result.append({
                    "index_code": code, "index_name": CORE_INDEXES[code],
                    "close": round(close, 2),
                    "pct_change": round((close - pre) / pre * 100, 2) if pre else 0.0,
                    "source": "local_cache",
                })
                continue
            df = df.sort_values("trade_date")
            r = df.iloc[-1]
            pre = float(r["pre_close"] or 0)
            close = float(r["close"] or 0)
            result.append({
                "index_code": code, "index_name": CORE_INDEXES[code],
                "close": round(close, 2),
                "pct_change": round((close - pre) / pre * 100, 2) if pre else 0.0,
                "source": "tushare",
            })
            # 顺手写入本地缓存（增量）
            local_cache.upsert_index_kline(code, df.tail(5))
        return result
