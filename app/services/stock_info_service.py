# -*- coding: utf-8 -*-
"""
个股深度信息服务（雪球侧边栏：股权信息 / 公司公告）
- Tushare：十大股东 top10_holders、十大流通股东 top10_floatholders、
           高管列表 stk_managers、股东人数 stk_holdernumber、
           高管增减持 stk_holdertrade、分红送股 dividend
- akshare(巨潮资讯)：年度报告 / 最新公告（含真实公告详情链接，可打开阅读原文）
- 缓存：股东类数据按财报季更新，缓存 1 小时；公告类缓存 10 分钟；基金持股按季度更新缓存 24 小时
"""
import logging

logger = logging.getLogger(__name__)

from app.utils.cache import cached

_LONG_TTL = 3600     # 股东/高管/分红等财报类数据
_SHORT_TTL = 600     # 公告类
_FUND_TTL = 86400    # 基金持股（季度数据，扫描全表成本高）


def _sym(code: str) -> str:
    """600043.SH → 600043（巨潮接口用 6 位代码）"""
    return code.split(".")[0]


def _periods():
    """最近 8 个财报报告期（YYYYMMDD，从最近的 6-30/9-30/12-31/3-31 往回推）"""
    from datetime import date
    today = date.today()
    out = []
    y, m = today.year, today.month
    while len(out) < 8:
        for mm in (12, 9, 6, 3):
            if (y, mm) < (y if mm != 12 else y, m) or True:
                pass
        # 简化：枚举近两年的四个报告期
        for mm in (12, 9, 6, 3):
            out.append(f"{y}{mm:02d}30" if mm == 6 else f"{y}{mm:02d}31" if mm in (3, 12) else f"{y}{mm:02d}30")
        y -= 1
    # 按日期倒序
    return sorted(set(out), reverse=True)


class StockInfoService:

    @staticmethod
    def get(code: str, kind: str):
        fn = {
            "top10_holders": StockInfoService.top10_holders,
            "top10_float": StockInfoService.top10_float,
            "fund_holdings": StockInfoService.fund_holdings,
            "fund_flow": StockInfoService.fund_flow,
            "news": StockInfoService.news,
            "holder_number": StockInfoService.holder_number,
            "holder_trade": StockInfoService.holder_trade,
            "dividend": StockInfoService.dividend,
            "managers": StockInfoService.managers,
            "annual_reports": StockInfoService.annual_reports,
            "announcements": StockInfoService.announcements,
        }.get(kind)
        if not fn:
            return None
        # 缓存 ttl 按数据类别区分：公告/新闻/资金流 10 分钟，基金持股 24 小时，其余财报类 1 小时
        ttl = _SHORT_TTL if kind in ("annual_reports", "announcements", "news", "fund_flow") else (
            _FUND_TTL if kind == "fund_holdings" else _LONG_TTL)

        def _load():
            try:
                return fn(code)
            except Exception as e:
                logger.warning(f"stock_info {kind}({code}) 失败: {str(e)[:100]}")
                return None
        return cached((kind, code), ttl, _load)

    # ---------- Tushare：十大股东 / 十大流通股东 ----------
    @staticmethod
    def _top10(code, api_name):
        from app.datasources.tushare_client import ts_client
        rows = []
        for period in _periods()[:6]:
            try:
                df = ts_client.call(api_name, ts_code=code, period=period)
            except Exception:
                continue
            if df is not None and not df.empty:
                rows = df.to_dict("records")
                break
        if not rows:
            return {"end_date": "", "holders": []}
        r0 = rows[0]
        return {
            "end_date": str(r0.get("end_date", "")),
            "ann_date": str(r0.get("ann_date", "")),
            "holders": [
                {
                    "name": r.get("holder_name"),
                    "amount": round(float(r["hold_amount"]) / 10000, 2) if r.get("hold_amount") == r.get("hold_amount") else None,  # 万股
                    "ratio": round(float(r["hold_ratio"]), 2) if r.get("hold_ratio") == r.get("hold_ratio") else None,
                    "float_ratio": round(float(r["hold_float_ratio"]), 2) if r.get("hold_float_ratio") == r.get("hold_float_ratio") else None,
                    "change": round(float(r["hold_change"]) / 10000, 2) if r.get("hold_change") == r.get("hold_change") else None,  # 万股
                    "type": r.get("holder_type") or "",
                } for r in rows
            ],
        }

    @staticmethod
    def top10_holders(code):
        return StockInfoService._top10(code, "top10_holders")

    @staticmethod
    def top10_float(code):
        return StockInfoService._top10(code, "top10_floatholders")

    # ---------- 股东人数 ----------
    @staticmethod
    def holder_number(code):
        from app.datasources.tushare_client import ts_client
        df = ts_client.call("stk_holdernumber", ts_code=code)
        if df is None or df.empty:
            return []
        df = df.sort_values("end_date", ascending=False).head(8)
        return [{"end_date": str(r["end_date"]),
                 "num": int(r["holder_num"])}
                for r in df.to_dict("records")]

    # ---------- 高管增减持 ----------
    @staticmethod
    def holder_trade(code):
        from app.datasources.tushare_client import ts_client
        df = ts_client.call("stk_holdertrade", ts_code=code)
        if df is None or df.empty:
            return []
        df = df.sort_values("ann_date", ascending=False).head(15)
        out = []
        for r in df.to_dict("records"):
            vol = r.get("change_vol")
            out.append({
                "ann_date": str(r.get("ann_date", "")),
                "holder": r.get("holder_name") or "",
                "in_de": "增持" if r.get("in_de") == "IN" else "减持" if r.get("in_de") == "DE" else (r.get("in_de") or ""),
                "vol": round(float(vol) / 10000, 2) if vol == vol else None,  # 万股
                "ratio": round(float(r["change_ratio"]), 2) if r.get("change_ratio") == r.get("change_ratio") else None,
                "price": round(float(r["avg_price"]), 2) if r.get("avg_price") == r.get("avg_price") else None,
            })
        return out

    # ---------- 分红送股 ----------
    @staticmethod
    def dividend(code):
        from app.datasources.tushare_client import ts_client
        df = ts_client.call("dividend", ts_code=code)
        if df is None or df.empty:
            return []
        df = df.sort_values("end_date", ascending=False).head(12)
        out = []
        for r in df.to_dict("records"):
            cash = r.get("cash_div_tax")
            stk = r.get("stk_div")
            out.append({
                "end_date": str(r.get("end_date", "")),
                "proc": r.get("div_proc") or "",
                "cash": round(float(cash), 3) if cash == cash else None,        # 每10股派息(元,含税)
                "stock": round(float(stk), 3) if stk == stk else None,          # 每10股转增/送股
                "ex_date": str(r.get("ex_date") or "") if r.get("ex_date") == r.get("ex_date") else "",
            })
        return out

    # ---------- 公司高管 ----------
    @staticmethod
    def managers(code):
        from app.datasources.tushare_client import ts_client
        df = ts_client.call("stk_managers", ts_code=code)
        if df is None or df.empty:
            return []
        # 在任者（end_date 为空）优先，按任职开始倒序，取前 30
        d = df[df["end_date"].isna()] if "end_date" in df.columns else df
        if d.empty:
            d = df
        d = d.sort_values("begin_date", ascending=False).head(30)
        return [{"name": r.get("name"), "title": (r.get("title") or "").replace("\\", " / "),
                 "gender": "男" if r.get("gender") == "M" else "女" if r.get("gender") == "F" else "",
                 "edu": r.get("edu") or "", "begin_date": str(r.get("begin_date") or "")}
                for r in d.to_dict("records")]

    # ---------- 基金持股（东方财富数据中心，按报告期全表扫描后过滤） ----------
    @staticmethod
    def _latest_report_dates():
        """最近两个已披露报告期（Q1/中报/Q3/年报）"""
        from datetime import date
        today = date.today()
        cands = []
        for y in (today.year, today.year - 1):
            cands += [f"{y}-03-31", f"{y}-06-30", f"{y}-09-30", f"{y}-12-31"]
        past = [d for d in sorted(cands, reverse=True) if d < today.isoformat()]
        return past[:2]  # 最近一期可能尚未披露完，备用上一期

    @staticmethod
    def fund_holdings(code):
        import requests
        sym = _sym(code)
        url = "http://data.eastmoney.com/dataapi/zlsj/list"
        result = {"report_date": "", "found": False, "rows": []}

        def scan(date_str):
            page = 1
            while True:
                r = requests.get(url, params={
                    "date": date_str, "type": "1", "zjc": "0",
                    "sortField": "HOULD_NUM", "sortDirec": "1",
                    "pageNum": page, "pageSize": "500", "p": page, "pageNo": page,
                }, timeout=15)
                j = r.json()
                data = j.get("data") or []
                for row in data:
                    if row.get("SECURITY_CODE") == sym:
                        shares = row.get("TOTAL_SHARES") or 0
                        value = row.get("HOLD_VALUE") or 0
                        cha = row.get("HOLDCHA_NUM")
                        return {
                            "report_date": date_str,
                            "found": True,
                            "rows": [{
                                "fund_count": row.get("HOULD_NUM"),
                                "shares_wan": round(shares / 10000, 1) if shares else None,      # 万股
                                "value_yi": round(value / 100000000, 2) if value else None,      # 亿元
                                "total_ratio": round(row["TOTALSHARES_RATIO"], 2) if row.get("TOTALSHARES_RATIO") else None,
                                "free_ratio": round(row["FREESHARES_RATIO"], 2) if row.get("FREESHARES_RATIO") else None,
                                "cha": row.get("HOLDCHA") or "",
                                "cha_wan": round(cha / 10000, 1) if cha else None,               # 万股
                            }],
                        }
                total = j.get("pages") or 1
                if isinstance(total, list):
                    total = len(total) or 1
                if page >= total or not data:
                    return None
                page += 1

        for date_str in StockInfoService._latest_report_dates():
            hit = scan(date_str)
            if hit:
                return hit
        return result

    # ---------- 资金流向（新浪 MoneyFlow，0积分；Tushare moneyflow 需2000积分故不采用） ----------
    @staticmethod
    def fund_flow(code):
        import json as _json
        from app.datasources.web_client import http_get
        from app.services.minute_service import _sina_symbol
        r = http_get(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_lscjfb",
            params={"page": 1, "num": 10, "sort": "opendate", "asc": 0,
                    "daima": _sina_symbol(code)},
            headers={"Referer": "https://finance.sina.com.cn"})
        rows = _json.loads(r.text)
        out = []
        for x in rows:
            w = lambda k: (float(x[k]) / 1e4 if x.get(k) not in (None, "") else None)  # 元→万元
            out.append({
                "date": x.get("opendate", ""),
                "close": float(x["trade"]) if x.get("trade") else None,
                "pct": round(float(x["changeratio"]) * 100, 2) if x.get("changeratio") else None,
                "main_wan": round((float(x["r0_net"]) + float(x["r1_net"])) / 1e4, 1)
                            if x.get("r0_net") and x.get("r1_net") else None,   # 主力=超大+大
                "xl_wan": w("r0_net"), "l_wan": w("r1_net"),
                "m_wan": w("r2_net"), "s_wan": w("r3_net"),
                "ratio": round(float(x["ratioamount"]) * 100, 2) if x.get("ratioamount") else None,
            })
        return out

    # ---------- 个股相关新闻（东财搜索，0积分；参数串与东财网页端完全一致） ----------
    @staticmethod
    def news(code):
        import json as _json
        import re as _re
        from app.datasources.web_client import http_get
        param = ('{"uid":",'
                 + f'"keyword":"{_sym(code)}"'
                 + ',"type":["cmsArticleWebOld"],"client":"web","clientType":"web","clientVersion":"curr",'
                 '"param":{"cmsArticleWebOld":{"searchScope":"default","sort":"default","pageIndex":1,'
                 '"pageSize":100,"preTag":"<em>","postTag":"</em>"}}}')
        r = http_get(
            "http://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "jQuery3510875346244069884_1668256937995", "param": param,
                    "_": "1668256937996"},
            headers={"Referer": "https://so.eastmoney.com"})
        t = r.text
        data = _json.loads(t[t.find("(") + 1:t.rindex(")")])
        arts = (data.get("result") or {}).get("cmsArticleWebOld") or []
        strip = lambda s: _re.sub(r"</?em>", "", str(s or ""))
        return [{"title": strip(a.get("title")),
                 "content": strip(a.get("content"))[:100],
                 "time": str(a.get("date") or ""),
                 "source": str(a.get("mediaName") or ""),
                 "url": str(a.get("url") or "")}
                for a in arts]

    # ---------- 巨潮资讯：年度报告 / 最新公告（akshare） ----------
    @staticmethod
    def _cninfo(code, category, start, limit):
        import akshare as ak
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=_sym(code), market="沪深京", category=category,
            start_date=start, end_date=time.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        return [{"title": r["公告标题"], "date": str(r["公告时间"]),
                 "url": r["公告链接"]}
                for r in df.head(limit).to_dict("records")]

    @staticmethod
    def annual_reports(code):
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(days=365 * 4)).strftime("%Y%m%d")
        return StockInfoService._cninfo(code, "年报", start, 10)

    @staticmethod
    def announcements(code):
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        return StockInfoService._cninfo(code, "", start, 25)
