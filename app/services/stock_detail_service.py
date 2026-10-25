# -*- coding: utf-8 -*-
"""
个股详情服务（模仿雪球个股页信息维度）
数据源（Tushare）：
- stock_basic   ：名称 / 行业 / 地区 / 板块 / 上市日期
- daily         ：最新价 / 开高低 / 昨收 / 成交量额 / 涨跌幅 → 推算涨停跌停 / 振幅
- daily_basic   ：换手率 / 市盈率TTM / 市净率 / 股息率 / 总股本 / 流通股本 / 总市值 / 流通市值
- daily(365天)  ：52周最高 / 52周最低
- stock_company ：公司简介 / 主营业务 / 官网 / 董事长 / 员工人数
策略：单只股票内存缓存 5 分钟；各数据源独立降级，任一失败不影响其他展示
"""
import logging

from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.datasources.tushare_client import get_target_trade_date, ts_client
from app.services.market_service import MarketService
from app.utils.cache import cached

_CACHE_TTL = 300     # 5 分钟


def _safe_float(v, nd=2):
    try:
        if v is None or v != v:
            return None
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _limit_pct(stock_code: str, name: str) -> float:
    """涨跌停幅度：科创板/创业板 20%，ST 5%，北交所 30%，主板 10%"""
    if stock_code.endswith(".BJ"):
        return 30.0
    if stock_code.startswith(("688", "300", "301")):
        return 20.0
    if name and "ST" in name.upper():
        return 5.0
    return 10.0


class StockDetailService:

    @staticmethod
    def get_detail(stock_code: str) -> dict:
        return cached(("stock_detail", stock_code), _CACHE_TTL,
                      lambda: StockDetailService._fetch(stock_code))

    @staticmethod
    def _fetch(stock_code: str) -> dict:
        if not stock_code:
            return {}
        d = {"stock_code": stock_code}

        # ---------- 基础信息（含名称，失败时也能给出代码） ----------
        basic_df = MarketService.get_stock_basic()
        name, industry, area, market, list_date = stock_code, "", "", "", ""
        if basic_df is not None and not basic_df.empty:
            row = basic_df[basic_df["ts_code"] == stock_code]
            if not row.empty:
                r = row.iloc[0]
                name = r.get("name") or stock_code
                industry = r.get("industry") or ""
                area = r.get("area") or ""
                market = r.get("market") or ""
                list_date = str(r.get("list_date") or "")
        d.update(stock_name=name, industry=industry, area=area,
                 market=market,
                 list_date=f"{list_date[:4]}-{list_date[4:6]}-{list_date[6:8]}"
                 if len(list_date) == 8 and list_date.isdigit() else list_date)

        # ---------- 最新行情（daily，含 open；失败降级 realtime/local） ----------
        trade_date = ""
        try:
            trade_date = get_target_trade_date()
        except Exception:
            trade_date = datetime.now().strftime("%Y%m%d")

        day_df = None
        for i in range(6):
            cur = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = ts_client.call("daily", ts_code=stock_code, trade_date=cur,
                                    fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount,pct_chg")
            except Exception:
                df = None
            if df is not None and not df.empty:
                day_df = df.iloc[-1]
                trade_date = cur
                break

        if day_df is not None:
            close = _safe_float(day_df["close"])
            pre = _safe_float(day_df["pre_close"])
            change = round(close - pre, 2) if (close is not None and pre) else None
            high, low = _safe_float(day_df["high"]), _safe_float(day_df["low"])
            amplitude = round((high - low) / pre * 100, 2) if (high and low and pre) else None
            lim = _limit_pct(stock_code, name)
            d.update(
                trade_date=f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                price=close, change=change,
                pct_change=_safe_float(day_df["pct_chg"]),
                open=_safe_float(day_df["open"]), high=high, low=low, pre_close=pre,
                limit_up=round(pre * (1 + lim / 100), 2) if pre else None,
                limit_down=round(pre * (1 - lim / 100), 2) if pre else None,
                volume_wan=_safe_float(day_df["vol"]) and round(float(day_df["vol"]) / 10000, 2),
                amount_yi=_safe_float(day_df["amount"]) and round(float(day_df["amount"]) / 100000, 2),
                amplitude=amplitude,
            )

        # ---------- 估值指标（daily_basic） ----------
        try:
            db = ts_client.call(
                "daily_basic", ts_code=stock_code, trade_date=trade_date.replace("-", ""),
                fields="turnover_rate,pe_ttm,pb,dv_ttm,total_share,float_share,total_mv,circ_mv",
            )
            if db is not None and not db.empty:
                b = db.iloc[-1]
                # 单位：市值/万元→亿元，股本/万股→亿股
                d.update(
                    turnover_rate=_safe_float(b["turnover_rate"]),
                    pe_ttm=_safe_float(b["pe_ttm"]),
                    pb=_safe_float(b["pb"]),
                    dv_ttm=_safe_float(b["dv_ttm"]),
                    total_mv_yi=_safe_float(b["total_mv"]) and round(float(b["total_mv"]) / 10000, 2),
                    circ_mv_yi=_safe_float(b["circ_mv"]) and round(float(b["circ_mv"]) / 10000, 2),
                    total_share_yi=_safe_float(b["total_share"]) and round(float(b["total_share"]) / 10000, 2),
                    float_share_yi=_safe_float(b["float_share"]) and round(float(b["float_share"]) / 10000, 2),
                )
        except Exception as e:
            logger.warning(f"⚠️ daily_basic 不可用({stock_code}): {str(e)[:80]}")

        # ---------- 52周最高/最低（daily 365 天） ----------
        try:
            start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
            hist = ts_client.call("daily", ts_code=stock_code, start_date=start,
                                  fields="high,low")
            if hist is not None and not hist.empty:
                d["high_52w"] = _safe_float(hist["high"].max())
                d["low_52w"] = _safe_float(hist["low"].min())
        except Exception as e:
            logger.warning(f"⚠️ 52周高低不可用({stock_code}): {str(e)[:80]}")

        # ---------- 公司简介（stock_company） ----------
        try:
            comp = ts_client.call(
                "stock_company", ts_code=stock_code,
                fields="introduction,main_business,website,chairman,employees,reg_capital",
            )
            if comp is not None and not comp.empty:
                c = comp.iloc[-1]
                intro = str(c.get("introduction") or "").strip()
                d.update(
                    introduction=intro or "暂无简介",
                    main_business=str(c.get("main_business") or "").strip(),
                    website=str(c.get("website") or "").strip(),
                    chairman=str(c.get("chairman") or "").strip(),
                    employees=int(c["employees"]) if str(c.get("employees") or "").strip().replace(".0", "").isdigit() else None,
                )
        except Exception as e:
            logger.warning(f"⚠️ 公司简介不可用({stock_code}): {str(e)[:80]}")

        return d
