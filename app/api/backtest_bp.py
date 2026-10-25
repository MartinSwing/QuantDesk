# -*- coding: utf-8 -*-
"""回测接口：策略元数据 / 执行回测"""
import re
from datetime import date, datetime

from flask import Blueprint, request
from flask_login import login_required

from app.services.backtest_service import (
    BacktestError,
    BacktestService,
    STRATEGIES,
    strategy_meta,
)
from app.utils.response import fail, ok, parse_int

backtest_bp = Blueprint("backtest", __name__, url_prefix="/api/backtest")

_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ)$")
_METRICS = {"return_pct", "annualized_pct", "excess_pct",
            "max_drawdown_pct", "sharpe", "win_rate_pct"}


def _valid_date(s) -> date:
    return datetime.strptime(str(s), "%Y-%m-%d").date()


@backtest_bp.route("/strategies")
@login_required
def strategies():
    """策略元数据（前端动态渲染参数表单）"""
    return ok(strategy_meta())


@backtest_bp.route("/run", methods=["POST"])
@login_required
def run():
    data = request.get_json(silent=True) or {}

    code = str(data.get("stock_code", "")).strip().upper()
    if not _CODE_RE.match(code):
        return fail("股票代码格式应为 600000.SH / 000001.SZ")

    strategy = str(data.get("strategy", "")).strip()
    if strategy not in STRATEGIES:
        return fail("不支持的策略类型")

    try:
        start = _valid_date(data.get("start_date", ""))
        end = _valid_date(data.get("end_date", ""))
    except (TypeError, ValueError):
        return fail("日期格式应为 YYYY-MM-DD")

    today = date.today()
    if start >= end:
        return fail("开始日期必须早于结束日期")
    if (end - start).days > 365 * 5:
        return fail("回测区间最长 5 年")
    if end > today:
        return fail("结束日期不能晚于今天")

    capital = parse_int(data.get("initial_capital", 100000),
                        "initial_capital", 10000, 10000000, 100000)

    try:
        result = BacktestService.run(code, strategy, data.get("params") or {},
                                      start.isoformat(), end.isoformat(), capital)
    except BacktestError as e:
        return fail(str(e))
    except Exception:
        from flask import current_app
        current_app.logger.exception("回测执行失败: %s %s", code, strategy)
        return fail("回测执行失败，行情数据可能暂不可用，请稍后重试")
    return ok(result)


def _common_args(data):
    """提取 compare/optimize 共用的入参并校验，返回 (code, start, end, capital) 或错误响应"""
    code = str(data.get("stock_code", "")).strip().upper()
    if not _CODE_RE.match(code):
        return None, fail("股票代码格式应为 600000.SH / 000001.SZ")
    try:
        start = _valid_date(data.get("start_date", ""))
        end = _valid_date(data.get("end_date", ""))
    except (TypeError, ValueError):
        return None, fail("日期格式应为 YYYY-MM-DD")
    if start >= end:
        return None, fail("开始日期必须早于结束日期")
    if (end - start).days > 365 * 5:
        return None, fail("回测区间最长 5 年")
    capital = parse_int(data.get("initial_capital", 100000),
                        "initial_capital", 10000, 10000000, 100000)
    return (code, start, end, capital), None


@backtest_bp.route("/compare", methods=["POST"])
@login_required
def compare():
    """多策略横向对比：同一标的/区间跑多个策略"""
    data = request.get_json(silent=True) or {}
    args, err = _common_args(data)
    if err:
        return err
    code, start, end, capital = args

    strategies = data.get("strategies") or []
    strategies = [s for s in strategies if s in STRATEGIES]
    if not strategies:
        return fail("请至少选择一个有效策略")
    if len(strategies) > 10:
        return fail("单次对比最多 10 个策略")

    try:
        result = BacktestService.compare(code, strategies, start.isoformat(),
                                         end.isoformat(), capital,
                                         data.get("params_map") or {})
    except BacktestError as e:
        return fail(str(e))
    except Exception:
        from flask import current_app
        current_app.logger.exception("策略对比失败: %s", code)
        return fail("对比执行失败，行情数据可能暂不可用，请稍后重试")
    return ok(result)


@backtest_bp.route("/optimize", methods=["POST"])
@login_required
def optimize():
    """参数寻优：网格搜索前 2 个参数，返回 2D 指标矩阵（热力图）"""
    data = request.get_json(silent=True) or {}
    args, err = _common_args(data)
    if err:
        return err
    code, start, end, capital = args

    strategy = str(data.get("strategy", "")).strip()
    if strategy not in STRATEGIES:
        return fail("不支持的策略类型")
    metric = str(data.get("metric", "return_pct")).strip()
    if metric not in _METRICS:
        return fail("不支持的指标")
    grid_points = parse_int(data.get("grid_points", 6), "grid_points", 3, 10, 6)

    try:
        result = BacktestService.optimize(code, strategy, start.isoformat(),
                                          end.isoformat(), capital, metric, grid_points)
    except BacktestError as e:
        return fail(str(e))
    except Exception:
        from flask import current_app
        current_app.logger.exception("参数寻优失败: %s %s", code, strategy)
        return fail("寻优执行失败，行情数据可能暂不可用，请稍后重试")
    return ok(result)
