# -*- coding: utf-8 -*-
"""
热点复盘服务：龙虎榜 + 涨停/炸板/跌停情绪
- 龙虎榜：主源 Tushare top_list（每日龙虎榜明细，实测积分可用）→ 降级 akshare 东财
- 情绪：akshare stock_zt_pool_em（涨停）/ zbgc_em（炸板）/ dtgc_em（跌停）
- 目标日无数据自动回溯最近交易日；成功缓存 5~10 分钟，失败负缓存 60s（防限流连打）
"""
import logging

logger = logging.getLogger(__name__)

from app.datasources.tushare_client import ts_client, get_target_trade_date
from app.datasources.web_client import ak_call
from app.utils.cache import cached

_LHB_TTL = 600
_SENT_TTL = 300
_NEG_TTL = 60   # 失败负缓存：60 秒内不再打上游接口


def _f(v, nd=2):
    try:
        if v is None or v != v:
            return None
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _t(v):
    """092500 → 09:25:00"""
    s = str(v or "")
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}" if len(s) == 6 and s.isdigit() else s


def _target_dates():
    """目标交易日 + 向前回溯 3 个自然日（应对数据滞后）"""
    from datetime import datetime, timedelta
    try:
        start = get_target_trade_date()
    except Exception:
        start = datetime.now().strftime("%Y%m%d")
    day = datetime.strptime(start, "%Y%m%d")
    return [(day - timedelta(days=i)).strftime("%Y%m%d") for i in range(4)]


class MarketHotService:

    # ---------- 龙虎榜 ----------
    @staticmethod
    def get_lhb() -> dict:
        return cached(("lhb",), _LHB_TTL, MarketHotService._lhb, neg_ttl=_NEG_TTL)

    @staticmethod
    def _lhb() -> dict:
        # 主源：Tushare top_list（每日龙虎榜明细）；全败降级 akshare 东财
        for date in _target_dates():
            try:
                df = ts_client.call("top_list", trade_date=date)
            except Exception as e:
                logger.warning(f"⚠️ 龙虎榜tushare不可用({date}): {str(e)[:80]}", flush=True)
                continue
            if df is None or df.empty:
                continue
            rows = []
            for r in df.to_dict("records"):
                rows.append({
                    "code": r.get("ts_code"), "name": r.get("name"),
                    "reason": r.get("reason") or "",
                    "close": _f(r.get("close")),
                    "pct": _f(r.get("pct_change")),
                    # tushare top_list 金额单位为元
                    "net_buy_yi": _f((r.get("net_amount") or 0) / 1e8),
                    "buy_yi": _f((r.get("l_buy") or 0) / 1e8),
                    "sell_yi": _f((r.get("l_sell") or 0) / 1e8),
                    "lhb_ratio": _f(r.get("net_rate")),
                    "turnover": _f(r.get("turnover_rate")),
                })
            rows.sort(key=lambda x: x["net_buy_yi"] or 0, reverse=True)
            return {"date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
                    "rows": rows, "source": "Tushare-龙虎榜每日明细"}
        return MarketHotService._lhb_akshare()

    @staticmethod
    def _lhb_akshare() -> dict:
        """降级源：akshare 东财龙虎榜"""
        import akshare as ak
        for date in _target_dates():
            try:
                df = ak_call(ak.stock_lhb_detail_em,
                             start_date=date, end_date=date)
            except Exception as e:
                logger.warning(f"⚠️ 龙虎榜不可用({date}): {str(e)[:80]}", flush=True)
                continue
            if df is None or df.empty:
                continue
            rows = []
            for r in df.to_dict("records"):
                rows.append({
                    "code": r.get("代码"), "name": r.get("名称"),
                    "reason": r.get("解读") or r.get("上榜原因") or "",
                    "close": _f(r.get("收盘价")),
                    "pct": _f(r.get("涨跌幅")),
                    "net_buy_yi": _f((r.get("龙虎榜净买额") or 0) / 1e8),
                    "buy_yi": _f((r.get("龙虎榜买入额") or 0) / 1e8),
                    "sell_yi": _f((r.get("龙虎榜卖出额") or 0) / 1e8),
                    "lhb_ratio": _f(r.get("净买额占总成交比")),
                    "turnover": _f(r.get("换手率")),
                })
            rows.sort(key=lambda x: x["net_buy_yi"] or 0, reverse=True)
            return {"date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
                    "rows": rows, "source": "东方财富-龙虎榜"}
        return {}

    # ---------- 涨停/炸板/跌停情绪 ----------
    @staticmethod
    def get_sentiment() -> dict:
        return cached(("sentiment",), _SENT_TTL, MarketHotService._sentiment, neg_ttl=_NEG_TTL)

    @staticmethod
    def _sentiment() -> dict:
        import akshare as ak
        for date in _target_dates():
            pools = {}
            try:
                zt = ak_call(ak.stock_zt_pool_em, date=date)
                zb = ak_call(ak.stock_zt_pool_zbgc_em, date=date)
                dt = ak_call(ak.stock_zt_pool_dtgc_em, date=date)
            except Exception as e:
                logger.warning(f"⚠️ 涨停池不可用({date}): {str(e)[:80]}")
                continue
            if zt is None or zt.empty:
                continue
            pools["zt"] = [MarketHotService._zt_row(r) for r in zt.to_dict("records")]
            pools["zt"].sort(key=lambda x: x["liuban"], reverse=True)
            pools["zb"] = [MarketHotService._zb_row(r) for r in (zb.to_dict("records") if zb is not None and not zb.empty else [])]
            pools["dt"] = [MarketHotService._dt_row(r) for r in (dt.to_dict("records") if dt is not None and not dt.empty else [])]
            max_lb = max((x["liuban"] for x in pools["zt"]), default=0)
            industries = {}
            for x in pools["zt"]:
                industries[x["industry"]] = industries.get(x["industry"], 0) + 1
            top_ind = sorted(industries.items(), key=lambda kv: kv[1], reverse=True)[:5]
            return {
                "date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
                "summary": {
                    "zt_count": len(pools["zt"]),
                    "zb_count": len(pools["zb"]),
                    "dt_count": len(pools["dt"]),
                    "max_liuban": max_lb,
                    "top_industries": top_ind,
                },
                **pools,
                "source": "东方财富-涨停板行情",
            }
        return {}

    @staticmethod
    def _zt_row(r):
        return {"code": r.get("代码"), "name": r.get("名称"),
                "pct": _f(r.get("涨跌幅")), "price": _f(r.get("最新价")),
                "amount_yi": _f((r.get("成交额") or 0) / 1e8),
                "turnover": _f(r.get("换手率")),
                "seal_yi": _f((r.get("封板资金") or 0) / 1e8),
                "first_seal": _t(r.get("首次封板时间")),
                "last_seal": _t(r.get("最后封板时间")),
                "zb_times": r.get("炸板次数") or 0,
                "liuban": r.get("连板数") or 1,
                "industry": r.get("所属行业") or "--"}

    @staticmethod
    def _zb_row(r):
        return {"code": r.get("代码"), "name": r.get("名称"),
                "pct": _f(r.get("涨跌幅")), "price": _f(r.get("最新价")),
                "zb_times": r.get("炸板次数") or 0,
                "first_seal": _t(r.get("首次封板时间")),
                "industry": r.get("所属行业") or "--"}

    @staticmethod
    def _dt_row(r):
        return {"code": r.get("代码"), "name": r.get("名称"),
                "pct": _f(r.get("涨跌幅")), "price": _f(r.get("最新价")),
                "liandie": r.get("连续跌停") or 1,
                "industry": r.get("所属行业") or "--"}
