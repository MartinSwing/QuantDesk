# -*- coding: utf-8 -*-
"""页面路由（渲染模板）"""
from flask import Blueprint, render_template
from flask_login import current_user, login_required

page_bp = Blueprint("page", __name__)


@page_bp.route("/")
@login_required
def index():
    return render_template("index.html")


@page_bp.route("/market")
@login_required
def market():
    return render_template("market.html")


@page_bp.route("/heatmap")
@login_required
def heatmap():
    """行业热力图页（全市场按行业聚合涨跌幅）"""
    return render_template("heatmap.html")


@page_bp.route("/industry/<industry_name>")
@login_required
def industry_detail(industry_name):
    """行业详情页：该行业全部个股列表（可按涨跌幅/成交额等排序）"""
    return render_template("industry_detail.html", industry_name=industry_name)


@page_bp.route("/hot")
@login_required
def hot():
    """涨停板复盘页：左右双栏（左涨停榜 / 右跌停榜）"""
    return render_template("hot.html")


@page_bp.route("/screener")
@login_required
def screener():
    """条件选股页：行业/涨跌幅/价格/市值/PE/PB/股息率/量额多维筛选"""
    return render_template("screener.html")


@page_bp.route("/backtest")
@login_required
def backtest():
    """策略回测页：双均线/MACD/KDJ 经典策略历史模拟"""
    return render_template("backtest.html")


@page_bp.route("/earnings")
@login_required
def earnings():
    """财报日历页：业绩快报 + 财报披露日历"""
    return render_template("earnings.html")


@page_bp.route("/hk")
@login_required
def hk():
    """港股行情页：实时行情 + 日K"""
    return render_template("hk.html")


@page_bp.route("/stock_kline/<stock_code>")
@login_required
def stock_kline(stock_code):
    return render_template("stock_kline.html", stock_code=stock_code)


# 个股信息详情页标题映射
INFO_TITLES = {
    "top10_holders": "十大股东",
    "top10_float": "十大流通股东",
    "fund_holdings": "基金持股",
    "fund_flow": "资金流向",
    "news": "个股新闻",
    "holder_number": "股东人数",
    "holder_trade": "高管增减持",
    "dividend": "分红送股",
    "managers": "公司高管",
    "annual_reports": "年度报告",
    "announcements": "最新公告",
}


@page_bp.route("/stock/<stock_code>/info/<kind>")
@login_required
def stock_info_page(stock_code, kind):
    """个股信息详情页：十大股东/基金持股/年报等，点击左侧导航进入"""
    if kind not in INFO_TITLES:
        return render_template("error.html", msg="未知的信息类型"), 404
    return render_template("stock_info.html", stock_code=stock_code,
                           kind=kind, info_title=INFO_TITLES[kind])


@page_bp.route("/index_kline/<index_code>")
@login_required
def index_kline(index_code):
    """指数详情页（核心指数点击进入）"""
    return render_template("index_kline.html", index_code=index_code)


@page_bp.route("/my_asset")
@login_required
def my_asset():
    return render_template("my_asset.html")


@page_bp.route("/my_trade")
@login_required
def my_trade():
    return render_template("my_trade.html")


@page_bp.route("/equity")
@login_required
def equity():
    """收益曲线页（账户净值 vs 沪深300基准）"""
    return render_template("performance.html")


@page_bp.route("/favorites")
@login_required
def favorites():
    return render_template("favorites.html")


@page_bp.route("/admin/users")
@login_required
def admin_users():
    if not current_user.is_admin:
        return render_template("error.html", msg="需要管理员权限"), 403
    return render_template("admin_users.html")
