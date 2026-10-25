# -*- coding: utf-8 -*-
"""行情接口：指数概览 / 实时行情 / 搜索 / K线 / 行业热力图 / 24-7快讯"""
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import login_required

from app.utils.response import ok, fail, parse_int
from app.services.heatmap_service import HeatmapService
from app.services.indicator_service import IndicatorService
from app.services.limit_rank_service import LimitRankService
from app.services.market_hot_service import MarketHotService
from app.services.market_service import CORE_INDEXES, MarketService
from app.services.minute_service import MinuteService
from app.services.moneyflow_service import get_north_flow, get_stock_moneyflow
from app.services.news_service import NewsService
from app.services.factor_service import FactorService
from app.services.earnings_calendar_service import get_calendar as earnings_calendar
from app.services.hk_service import get_hk_kline, get_hk_list
from app.services.regime_service import detect as detect_regime
from app.services.screener_service import ScreenerService
from app.services.valuation_service import get_history as valuation_history
from app.services.stock_detail_service import StockDetailService
from app.services.stock_info_service import StockInfoService

market_bp = Blueprint("market", __name__, url_prefix="/api/market")


@market_bp.route("/indexes")
@login_required
def indexes():
    return jsonify({"code": 0, "msg": "成功", "data": MarketService.get_index_overview()})


@market_bp.route("/regime")
@login_required
def regime():
    """市场状态识别：均线排列 + 波动率分位三分类（趋势上行/下行/震荡）"""
    index_code = (request.args.get("index") or "").strip().upper() or "000300.SH"
    return ok(detect_regime(index_code))


@market_bp.route("/valuation")
@login_required
def valuation():
    """价值投资指标历史：PE(TTM)/PB/股息率/总市值 + 当前值与历史分位"""
    code = (request.args.get("code") or "").strip().upper()
    if not code:
        return fail("请传入股票代码")
    years = parse_int(request.args.get("years", 2), "years", 1, 5, 2)
    result = valuation_history(code, years)
    if not result:
        return fail("暂无估值数据（次新股或数据暂不可用）")
    return ok(result)


@market_bp.route("/moneyflow")
@login_required
def moneyflow():
    """个股资金流：主力/超大单/大单/中单/小单逐日净流入"""
    code = (request.args.get("code") or "").strip().upper()
    if not code:
        return fail("请传入股票代码")
    days = parse_int(request.args.get("days", 60), "days", 10, 250, 60)
    result = get_stock_moneyflow(code, days)
    if not result:
        return fail("暂无资金流数据")
    return ok(result)


@market_bp.route("/north_flow")
@login_required
def north_flow():
    """北向资金（沪深港通）逐日净买入"""
    days = parse_int(request.args.get("days", 60), "days", 10, 250, 60)
    result = get_north_flow(days)
    if not result:
        return fail("暂无北向资金数据")
    return ok(result)


@market_bp.route("/factors", methods=["POST"])
@login_required
def factors():
    """多因子选股打分：估值(PE/PB)+价值(股息率)+动量(涨跌幅) 加权排名"""
    data = request.get_json(silent=True) or {}
    weights = data.get("weights") or {}
    industry = (data.get("industry") or "").strip()
    top_n = parse_int(data.get("top_n", 50), "top_n", 5, 200, 50)
    # 权重合法性：仅允许四个因子，数值 0~100
    w = {}
    for k in ("pe", "pb", "dv", "pct"):
        try:
            v = float(weights.get(k, 25))
        except (TypeError, ValueError):
            v = 25
        w[k] = max(0.0, min(100.0, v))
    if sum(w.values()) <= 0:
        return fail("因子权重不能全为 0")
    result = FactorService.rank(w, industry, top_n)
    if not result or not result.get("rows"):
        return fail("暂无数据，请稍后重试")
    return ok(result)


@market_bp.route("/earnings")
@login_required
def earnings():
    """财报日历：财报披露日历 + 业绩快报（营收/利润同比）"""
    return ok(earnings_calendar())


@market_bp.route("/hk_list")
@login_required
def hk_list():
    """港股实时行情列表"""
    rows = get_hk_list()
    if not rows:
        return fail("港股行情暂不可用，请稍后重试")
    return ok(rows)


@market_bp.route("/hk_kline")
@login_required
def hk_kline():
    """港股日K（symbol 5 位代码如 00700）"""
    symbol = (request.args.get("symbol") or "").strip()
    if not symbol:
        return fail("请传入港股代码")
    days = parse_int(request.args.get("days", 250), "days", 30, 1825, 250)
    kline = get_hk_kline(symbol, days)
    if not kline:
        return fail("暂无港股K线数据")
    return ok(kline)


@market_bp.route("/realtime", methods=["POST"])
@login_required
def realtime():
    data = request.get_json(silent=True) or {}
    codes = data.get("stock_codes", [])
    if not codes:
        return jsonify({"code": -1, "msg": "请传入股票代码列表", "data": []})
    quotes = MarketService.get_realtime_quotes(codes[:50])  # 上限50只，防滥用
    return jsonify({"code": 0, "msg": "成功", "data": quotes})


@market_bp.route("/search")
@login_required
def search():
    keyword = (request.args.get("q") or "").strip()
    if not keyword:
        return jsonify({"code": -1, "msg": "请输入关键词", "data": []})
    return jsonify({"code": 0, "msg": "成功", "data": MarketService.search_stocks(keyword)})


@market_bp.route("/kline", methods=["POST"])
@login_required
def stock_kline():
    data = request.get_json(silent=True) or {}
    code = data.get("stock_code", "")
    days = parse_int(data.get("days", 120), "days", 30, 1825, 120)
    if not code:
        return fail("请传入股票代码", data=[])
    start_d, end_d = _parse_range(data)
    if start_d is False:
        return fail("日期格式应为 YYYY-MM-DD")
    if start_d and (end_d - start_d).days > 365 * 5:
        return fail("时间区间最长 5 年")
    return ok(MarketService.get_stock_kline(code, days, start_d, end_d))


@market_bp.route("/index_kline", methods=["POST"])
@login_required
def index_kline():
    data = request.get_json(silent=True) or {}
    code = data.get("index_code", "")
    days = parse_int(data.get("days", 120), "days", 30, 1825, 120)
    if not code:
        return fail("请传入指数代码", data=[])
    return ok(MarketService.get_index_kline(code, days))


def _parse_range(data):
    """解析可选 start_date/end_date（YYYY-MM-DD）；格式错返回 (False, False)，
    未传返回 (None, None)；start>=end 视为格式错误"""
    s, e = data.get("start_date"), data.get("end_date")
    if not s and not e:
        return None, None
    if not s or not e:
        return False, False
    try:
        sd = datetime.strptime(str(s), "%Y-%m-%d")
        ed = datetime.strptime(str(e), "%Y-%m-%d")
    except (TypeError, ValueError):
        return False, False
    if sd >= ed:
        return False, False
    return sd, ed


@market_bp.route("/indicators", methods=["POST"])
@login_required
def indicators():
    """K线 + 技术指标四件套（MACD/KDJ/RSI/BOLL），kind: stock|index
    可选 start_date/end_date 指定区间（优先于 days）"""
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    kind = data.get("kind", "stock")
    if kind not in ("stock", "index"):
        kind = "stock"
    days = parse_int(data.get("days", 120), "days", 30, 1825, 120)
    if not code:
        return fail("请传入代码")
    start_d, end_d = _parse_range(data)
    if start_d is False:
        return fail("日期格式应为 YYYY-MM-DD")
    if start_d and (end_d - start_d).days > 365 * 5:
        return fail("时间区间最长 5 年")
    result = IndicatorService.get(code, kind, days, start_d, end_d)
    if not result:
        return fail("暂无K线数据")
    return jsonify({"code": 0, "msg": "成功", "data": result})


@market_bp.route("/indexes/list")
@login_required
def index_list():
    return jsonify({"code": 0, "msg": "成功",
                    "data": [{"index_code": k, "index_name": v} for k, v in CORE_INDEXES.items()]})


@market_bp.route("/heatmap")
@login_required
def heatmap():
    """行业热力图：按行业聚合全市场涨跌幅（Tushare）"""
    data = HeatmapService.get_heatmap_public()
    if not data:
        return jsonify({"code": -1, "msg": "热力图数据暂不可用（Tushare不可用），请稍后重试", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/industry_stocks")
@login_required
def industry_stocks():
    """行业个股列表：/industry/<name> 详情页数据源（复用热力图缓存）"""
    industry = (request.args.get("industry") or "").strip()
    if not industry:
        return jsonify({"code": -1, "msg": "请传入行业名称", "data": {}})
    data = HeatmapService.get_industry_stocks(industry)
    if not data:
        return jsonify({"code": -1, "msg": f"未找到行业「{industry}」数据，可能名称有误或数据暂不可用", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/screener")
@login_required
def screener():
    """多维度条件选股：行业/涨跌幅/涨跌额/价格/市值/PE/PB/股息率/成交量/成交额（服务端分页）"""
    page = parse_int(request.args.get("page"), "page", 1, None, 1)
    page_size = parse_int(request.args.get("page_size"), "page_size", 1, 100, 50)
    data = ScreenerService.screen(request.args.to_dict(), page=page, page_size=page_size)
    if not data:
        return fail("选股数据暂不可用（Tushare不可用），请稍后重试")
    return ok(data)


@market_bp.route("/limit_rank")
@login_required
def limit_rank():
    """涨跌榜：涨停/跌停榜（新浪webclient快照，仅沪主板/深主板/创业板/科创板，排除北交所）"""
    data = LimitRankService.get_limit_rank()
    if not data:
        return jsonify({"code": -1, "msg": "涨跌榜数据暂不可用，请稍后重试", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/news")
@login_required
def news():
    """24/7 全球财经快讯（akshare）"""
    limit = min(int(request.args.get("limit", 60)), 200)
    data = NewsService.get_latest_news(limit)
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/intraday")
@login_required
def intraday():
    """分时数据：1分钟线 + 均价线 + 昨收（新浪源）"""
    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    data = MinuteService.get_intraday(code)
    if not data:
        return jsonify({"code": -1, "msg": "分时数据暂不可用", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/bid_ask")
@login_required
def bid_ask():
    """实时五档盘口（新浪源）"""
    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    data = MinuteService.get_bid_ask(code)
    if not data:
        return jsonify({"code": -1, "msg": "盘口数据暂不可用", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/lhb")
@login_required
def lhb():
    """龙虎榜（最近交易日，东财 datacenter）"""
    data = MarketHotService.get_lhb()
    if not data:
        return jsonify({"code": -1, "msg": "龙虎榜数据暂不可用", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/zt_pool")
@login_required
def zt_pool():
    """涨停/炸板/跌停池 + 市场情绪（东财 datacenter）"""
    data = MarketHotService.get_sentiment()
    if not data:
        return jsonify({"code": -1, "msg": "涨停池数据暂不可用", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/stock_info")
@login_required
def stock_info():
    """个股深度信息：十大股东/流通股东/股东人数/高管增减持/分红/高管/年报/公告"""
    code = (request.args.get("code") or "").strip()
    kind = (request.args.get("kind") or "").strip()
    if not code or not kind:
        return jsonify({"code": -1, "msg": "请传入 code 与 kind", "data": None})
    data = StockInfoService.get(code, kind)
    if data is None:
        return jsonify({"code": -1, "msg": "数据暂不可用（接口权限或网络原因）", "data": None})
    return jsonify({"code": 0, "msg": "成功", "data": data})


@market_bp.route("/stock_detail")
@login_required
def stock_detail():
    """个股详情：行情 + 估值 + 52周高低 + 公司简介（雪球风格）"""
    code = (request.args.get("code") or "").strip()
    if not code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    data = StockDetailService.get_detail(code)
    if not data:
        return jsonify({"code": -1, "msg": "暂无该股票数据", "data": {}})
    return jsonify({"code": 0, "msg": "成功", "data": data})
