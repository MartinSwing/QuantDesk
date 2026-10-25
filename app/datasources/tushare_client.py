# -*- coding: utf-8 -*-
"""
Tushare 客户端封装
- 全局唯一 pro_api 实例
- 失败计数 + 冷却期：连续失败达到阈值后进入冷却，期间调用方应直接走本地缓存
- 交易日/目标交易日计算（17点规则）
"""
import threading
import time
from datetime import datetime, timedelta

import tushare as ts

from config import settings

_RULES = settings.MARKET_RULES


class TushareClient:
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._instance_lock:
            if not hasattr(cls, "_instance"):
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        ts.set_token(settings.TUSHARE_TOKEN)
        self.pro = ts.pro_api()
        self._fail_count = 0
        self._cooldown_until = 0
        self._lock = threading.Lock()
        self._trade_cal_cache = None  # {date_str: is_open}
        self._trade_cal_day = None

    # ---------- 可用性 ----------
    def available(self) -> bool:
        return time.time() >= self._cooldown_until

    def mark_success(self):
        with self._lock:
            self._fail_count = 0

    def mark_failure(self, reason: str = ""):
        with self._lock:
            self._fail_count += 1
            if self._fail_count >= _RULES["tushare_fail_threshold"]:
                self._cooldown_until = time.time() + _RULES["tushare_cooldown_seconds"]
                print(f"⚠️ Tushare 连续失败 {self._fail_count} 次，进入冷却期: {reason[:80]}")

    def call(self, api_name: str, **kwargs):
        """统一的接口调用入口，自动处理成败计数"""
        if not self.available():
            raise ConnectionError("Tushare处于冷却期，请使用本地缓存")
        try:
            fn = getattr(self.pro, api_name)
            result = fn(**kwargs)
            self.mark_success()
            return result
        except Exception as e:
            self.mark_failure(str(e))
            raise


# 全局单例
ts_client = TushareClient()


# ---------- 交易日历 ----------
def get_trade_calendar(days: int = 60) -> dict:
    """获取最近N天交易日历 {YYYYMMDD: is_open}，按天缓存"""
    today = datetime.now().strftime("%Y%m%d")
    if ts_client._trade_cal_cache is not None and ts_client._trade_cal_day == today:
        return ts_client._trade_cal_cache
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ts_client.call(
            "trade_cal", exchange="SSE", start_date=start, end_date=today,
            fields="cal_date,is_open",
        )
        cal = dict(zip(df["cal_date"], df["is_open"]))
        ts_client._trade_cal_cache = cal
        ts_client._trade_cal_day = today
        return cal
    except Exception:
        return ts_client._trade_cal_cache or {}


def get_target_trade_date() -> str:
    """
    目标交易日：
    - 交易日17:00前 → 上一交易日
    - 交易日17:00后 → 当日
    - 非交易日 → 最近上一交易日
    Tushare不可用时按自然日推算（跳过周末）
    """
    now = datetime.now()
    rule_time = datetime.strptime(_RULES["target_date_after"], "%H:%M").time()
    cal = get_trade_calendar()

    def prev_trade_day(d: datetime) -> str:
        d = d - timedelta(days=1)
        # 无日历时按周末推算，最多回溯10天
        for _ in range(10):
            key = d.strftime("%Y%m%d")
            if cal:
                if cal.get(key) == 1:
                    return key
            elif d.weekday() < 5:
                return key
            d = d - timedelta(days=1)
        return d.strftime("%Y%m%d")

    today_key = now.strftime("%Y%m%d")
    if cal.get(today_key) == 1 and now.time() >= rule_time:
        return today_key
    return prev_trade_day(now)
