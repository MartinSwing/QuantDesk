# -*- coding: utf-8 -*-
"""
行业热力图服务（Tushare 数据源）
思路：全市场当日行情(daily) + 股票基础信息(stock_basic 行业字段)
→ 按行业聚合平均涨跌幅 → 生成 ECharts treemap 数据（行业 → 成交额Top股票）
- 内存缓存 10 分钟，避免高频重复拉取全市场数据
- Tushare 失败时自动回溯最近几个交易日，仍失败则返回空由前端提示
"""
from app.datasources.tushare_client import ts_client
from app.services.market_service import MarketService
from app.utils.cache import cached
from app.utils.dates import backtrack

_CACHE_TTL = 600          # 缓存 10 分钟
_LOOKBACK_DAYS = 6        # 目标日无数据时最多回溯的自然日天数
_TOP_STOCKS_PER_INDUSTRY = 8  # 每个行业展示成交额 Top N 股票


class HeatmapService:

    @staticmethod
    def get_industry_heatmap() -> dict:
        """返回 {trade_date, industries:[...]}，缓存期内直接命中"""
        return cached(("heatmap",), _CACHE_TTL, HeatmapService._fetch)

    @staticmethod
    def get_heatmap_public() -> dict:
        """/heatmap 接口专用：剥离全量 stocks 字段的轻量副本（缓存对象不做修改）"""
        data = HeatmapService.get_industry_heatmap()
        if not data:
            return {}
        out = {"trade_date": data.get("trade_date"), "summary": data.get("summary"),
               "industries": [{k: v for k, v in ind.items() if k != "stocks"}
                              for ind in data.get("industries", [])]}
        return out

    @staticmethod
    def get_industry_stocks(industry: str) -> dict:
        """查询单个行业下全部个股（复用热力图缓存，当日数据）"""
        industry = (industry or "").strip()
        if not industry:
            return {}
        data = HeatmapService.get_industry_heatmap()
        if not data:
            return {}
        for ind in data.get("industries", []):
            if ind["name"] == industry:
                return {
                    "industry": industry,
                    "trade_date": data.get("trade_date"),
                    "avg_pct": ind["value"][1],
                    "amount_yi": ind["value"][0],
                    "stock_count": ind["stock_count"],
                    "stocks": ind["stocks"],
                }
        return {}

    @staticmethod
    def _fetch() -> dict:
        # 目标交易日 + 回溯（统一走公共 backtrack）
        daily, trade_date = backtrack(
            lambda cur: ts_client.call("daily", trade_date=cur,
                                       fields="ts_code,close,pct_chg,amount"),
            lookback=_LOOKBACK_DAYS)
        if daily is None:
            return {}

        basic_df = MarketService.get_stock_basic()
        if basic_df is None or basic_df.empty:
            return {}

        industry_map = dict(zip(basic_df["ts_code"], basic_df["industry"]))
        name_map = dict(zip(basic_df["ts_code"], basic_df["name"]))

        # 按行业聚合
        industries = {}
        for r in daily.itertuples(index=False):
            ind = industry_map.get(r.ts_code)
            # 行业缺失或为 NaN 的股票跳过（NaN 是真值，须单独判断）
            if not ind or ind != ind:
                continue
            # 过滤缺失/NaN 的涨跌幅，避免污染行业均值
            pct = float(r.pct_chg) if r.pct_chg is not None and r.pct_chg == r.pct_chg else 0.0
            amt = float(r.amount) / 100000 if r.amount is not None else 0.0  # 千元→亿元
            bucket = industries.setdefault(ind, {"stocks": [], "amt_sum": 0.0})
            sname = name_map.get(r.ts_code, r.ts_code)
            bucket["stocks"].append({
                "name": sname if sname == sname else r.ts_code,
                "code": r.ts_code,
                "close": round(float(r.close), 2) if r.close is not None and r.close == r.close else None,
                "pct": round(pct, 2),
                "amount": amt if amt == amt else 0.0,
            })
            bucket["amt_sum"] += amt if amt == amt else 0.0

        result = []
        up_cnt = down_cnt = 0
        for ind, bucket in industries.items():
            stocks = sorted(bucket["stocks"], key=lambda s: s["amount"], reverse=True)
            pcts = [s["pct"] for s in bucket["stocks"]]
            avg_pct = round(sum(pcts) / len(pcts), 2)
            if avg_pct >= 0:
                up_cnt += 1
            else:
                down_cnt += 1
            result.append({
                "name": ind,
                "value": [round(bucket["amt_sum"], 1), avg_pct],  # [成交额(亿), 平均涨跌幅%]
                "stock_count": len(stocks),
                # 全量个股（按成交额降序）：行业详情页 /industry_stocks 接口使用，
                # /heatmap 响应会剥离该字段以保持轻量
                "stocks": stocks,
                "top_stocks": stocks[:_TOP_STOCKS_PER_INDUSTRY],
            })

        result.sort(key=lambda x: x["value"][0], reverse=True)
        td = trade_date
        date_str = f"{td[:4]}-{td[4:6]}-{td[6:8]}" if td else ""
        return {
            "trade_date": date_str,
            "industries": result,
            "summary": {
                "industry_total": len(result),
                "industry_up": up_cnt,
                "industry_down": down_cnt,
                "stock_total": int(len(daily)),
                "top_industry": max(result, key=lambda x: x["value"][1])["name"] if result else "",
                "bottom_industry": min(result, key=lambda x: x["value"][1])["name"] if result else "",
            },
        }
