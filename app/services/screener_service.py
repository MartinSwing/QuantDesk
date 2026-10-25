# -*- coding: utf-8 -*-
"""
选股器服务（Tushare 数据源，600 积分档可用）
数据拼装：daily(涨跌/量额) + daily_basic(PE/PB/股息率/市值) + stock_basic(行业/名称)
→ 全市场快照 → 按多维度条件筛选（行业、涨跌幅、涨跌额、价格、市值、PE、PB、股息率、成交量、成交额）
- 内存缓存 10 分钟（与热力图同策略）
- 目标交易日无数据自动回溯最近几个交易日
"""
from app.datasources.tushare_client import ts_client
from app.services.market_service import MarketService
from app.utils.cache import cached
from app.utils.dates import backtrack

_CACHE_TTL = 600
_LOOKBACK_DAYS = 6

# 筛选字段 → (说明, 单位换算后的键)
NUM_FILTERS = ("pct", "chg", "price", "mv", "pe", "pb", "dv", "vol", "amount")


class ScreenerService:

    # ---------- 全市场快照 ----------
    @staticmethod
    def get_universe() -> dict:
        """返回 {trade_date, rows, industries}，缓存期内直接命中"""
        return cached(("screener_universe",), _CACHE_TTL, ScreenerService._fetch)

    @staticmethod
    def _fetch() -> dict:
        def _fetch_day(cur):
            d1 = ts_client.call("daily", trade_date=cur,
                                fields="ts_code,close,pre_close,pct_chg,vol,amount")
            d2 = ts_client.call("daily_basic", trade_date=cur,
                                fields="ts_code,close,pe_ttm,pb,dv_ratio,total_mv")
            if d1 is None or d1.empty or d2 is None or d2.empty:
                return None
            return (d1, d2)

        daily_pair, trade_date = backtrack(_fetch_day, lookback=_LOOKBACK_DAYS)
        if daily_pair is None:
            return {}
        daily, basic = daily_pair

        basic_df = MarketService.get_stock_basic()
        if basic_df is None or basic_df.empty:
            return {}
        ind_map = dict(zip(basic_df["ts_code"], basic_df["industry"]))
        name_map = dict(zip(basic_df["ts_code"], basic_df["name"]))

        # daily_basic 按 ts_code 建索引（hash 查找 O(1)）
        b = basic.set_index("ts_code")
        has_dup = b.index.duplicated().any()

        rows = []
        for r in daily.itertuples(index=False):
            code = r.ts_code
            ind = ind_map.get(code)
            # 行业缺失或 NaN（NaN 是真值，须单独判断）→ 归为 "--"
            if not ind or ind != ind:
                ind = "--"
            if code in b.index:
                brow = b.loc[code]
                # 同 code 多行（极端）取第一行
                if has_dup and getattr(brow, "ndim", 1) > 1:
                    brow = brow.iloc[0]
                pe = _f(brow.get("pe_ttm"))
                pb = _f(brow.get("pb"))
                dv = _f(brow.get("dv_ratio"))
                mv = _f(brow.get("total_mv"))
            else:
                pe = pb = dv = mv = None
            close = _f(r.close)
            pre = _f(r.pre_close)
            sname = name_map.get(code, code)
            if not sname or sname != sname:
                sname = code
            chg = round(close - pre, 2) if close is not None and pre is not None else None
            rows.append({
                "code": code,
                "name": sname,
                "industry": ind,
                "close": close,
                "chg": chg,                                            # 涨跌额(元)
                "pct": _f(r.pct_chg),                                  # 涨跌幅(%)
                "mv": round(mv / 1e4, 2) if mv is not None else None,  # 总市值(亿)，万元→亿
                "pe": pe,                                              # PE(TTM)
                "pb": pb,
                "dv": dv,                                              # 股息率(%)
                "vol": round(_f(r.vol) / 1e4, 2) if r.vol is not None else None,      # 成交量(万手)
                "amount": round(_f(r.amount) / 1e5, 2) if r.amount is not None else None,  # 成交额(亿)
            })

        rows.sort(key=lambda x: x["amount"] or 0, reverse=True)
        industries = sorted({r["industry"] for r in rows if r["industry"] != "--"})
        td = trade_date
        date_str = f"{td[:4]}-{td[4:6]}-{td[6:8]}" if td else ""
        return {"trade_date": date_str, "rows": rows, "industries": industries}

    # ---------- 条件筛选 ----------
    @staticmethod
    def screen(filters: dict, page: int = 1, page_size: int = 50) -> dict:
        """
        filters: {industry?, pct_min?, pct_max?, chg_min?, chg_max?, price_min?, price_max?,
                  mv_min?, mv_max?, pe_min?, pe_max?, pb_min?, pb_max?, dv_min?, dv_max?,
                  vol_min?, vol_max?, amount_min?, amount_max?}（数值均为字符串或数字）
        分页：内存筛选结果按 page/page_size 切片（服务端分页，替代一次性截断）
        """
        data = ScreenerService.get_universe()
        if not data:
            return {}
        page = max(1, int(page or 1))
        page_size = min(max(1, int(page_size or 50)), 100)
        conds = {}
        for f in NUM_FILTERS:
            lo = _num(filters.get(f"{f}_min"))
            hi = _num(filters.get(f"{f}_max"))
            if lo is not None or hi is not None:
                conds[f] = (lo, hi)
        industry = (filters.get("industry") or "").strip()

        matched = []
        for r in data["rows"]:
            if industry and r["industry"] != industry:
                continue
            ok = True
            for f, (lo, hi) in conds.items():
                v = r.get(f)
                # 值缺失（亏损无PE等）：只要设了任一边界即排除
                if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
                    ok = False
                    break
            if ok:
                matched.append(r)

        total = len(matched)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return {
            "trade_date": data["trade_date"],
            "total_market": len(data["rows"]),
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "rows": matched[start:start + page_size],
            "industries": data["industries"],
        }


def _f(v):
    """安全转 float，NaN/None → None"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _num(v):
    """筛选输入 → float 或 None"""
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None
