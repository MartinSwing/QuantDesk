# -*- coding: utf-8 -*-
"""
策略回测服务（自研轻量引擎，单标的，无状态）
- 策略：双均线交叉 / MACD金死叉 / KDJ超买超卖 / 布林带均值回归 /
        唐奇安通道突破（趋势） / 动量 / 网格交易
  信号口径与 indicator_service 完全一致（图上看到的金叉 = 回测成交的金叉）
- 信号语义：+1/-1 为全仓买/卖；|s|<1 为比例信号（买入用现金的 s 比例、
  卖出用持仓的 |s| 比例，网格策略使用）
- 撮合：T 日收盘计算信号 → T+1 日开盘价成交（防未来函数）
  100 股整手；佣金万2.5（最低5元双边）+ 印花税万5（仅卖出，TRADE_RULES）
- 绩效：总收益/年化/最大回撤/夏普/胜率/交易次数；基准沪深300（缺失降级上证指数）
- 结果缓存 10 分钟（utils/cache.py）
"""
import json
import math
from datetime import date, datetime, timedelta

import pandas as pd

from app.datasources.local_cache import (
    get_daily_kline,
    get_index_kline,
    upsert_daily_kline,
    upsert_index_kline,
)
from app.datasources.tushare_client import ts_client
from app.services.indicator_service import _ema, _rsv, _sma_cn
from app.utils.cache import cached

BENCHMARK_CODE = "000300.SH"      # 沪深300
BENCHMARK_FALLBACK = "000001.SH"  # 上证指数兜底
_RISK_FREE_ANNUAL = 0.02          # 无风险利率（夏普计算用）
_TTL = 600
# 参数寻优可选指标（summary 字段）
_METRICS = ("return_pct", "annualized_pct", "excess_pct", "max_drawdown_pct",
            "sharpe", "win_rate_pct")


class BacktestError(Exception):
    """回测参数/数据错误（API 层转 fail 响应）"""


# ---------------------------------------------------------------- 信号计算
def _sig_ma_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    short, long_ = p["short"], p["long"]
    ma_s = df["close"].rolling(short, min_periods=1).mean()
    ma_l = df["close"].rolling(long_, min_periods=1).mean()
    pos = (ma_s > ma_l).astype(float)          # 逐日目标持仓状态
    return pos.diff().fillna(0.0)              # +1 金叉买 / -1 死叉卖


def _sig_macd(df: pd.DataFrame, p: dict) -> pd.Series:
    close = df["close"]
    dif = _ema(close, 12) - _ema(close, 26)
    dea = dif.ewm(span=9, adjust=False).mean()
    pos = (dif > dea).astype(float)
    return pos.diff().fillna(0.0)              # +1 金叉买 / -1 死叉卖


def _sig_kdj(df: pd.DataFrame, p: dict) -> pd.Series:
    close = df["close"]
    rsv = _rsv(close, 9)
    k = _sma_cn(rsv, 3, 1)
    d = _sma_cn(k, 3, 1)
    j = 3 * k - 2 * d
    buy = (j < 20) & (j.shift(1).fillna(50) >= 20)    # 跌入超卖区
    sell = (j > 80) & (j.shift(1).fillna(50) <= 80)   # 升入超买区
    sig = pd.Series(0.0, index=df.index)
    sig[buy] = 1.0
    sig[sell] = -1.0
    return sig


def _sig_boll_revert(df: pd.DataFrame, p: dict) -> pd.Series:
    """布林带均值回归：收盘跌破下轨（超卖）买入，回升至中轨上方卖出"""
    n, k = int(p["n"]), p["k"]
    close = df["close"]
    mid = close.rolling(n, min_periods=1).mean()
    std = close.rolling(n, min_periods=1).std(ddof=0).fillna(0.0)
    lower = mid - k * std
    below = (close < lower).tolist()
    above_mid = (close >= mid).tolist()
    sig, in_pos = [0.0] * len(close), False
    for i in range(len(close)):
        if not in_pos and below[i]:
            sig[i], in_pos = 1.0, True
        elif in_pos and above_mid[i]:
            sig[i], in_pos = -1.0, False
    return pd.Series(sig)


def _sig_donchian(df: pd.DataFrame, p: dict) -> pd.Series:
    """唐奇安通道突破（海龟核心）：突破前 entry_n 日最高价买入，
    跌破前 exit_n 日最低价卖出。通道线 shift(1) 排除当日（防未来函数）"""
    entry_n, exit_n = int(p["entry_n"]), int(p["exit_n"])
    close = df["close"]
    upper = df["high"].rolling(entry_n, min_periods=1).max().shift(1)
    lower = df["low"].rolling(exit_n, min_periods=1).min().shift(1)
    c, u, l = close.tolist(), upper.tolist(), lower.tolist()
    sig, in_pos = [0.0] * len(c), False
    for i in range(len(c)):
        if u[i] is None or pd.isna(u[i]) or l[i] is None or pd.isna(l[i]):
            continue
        if not in_pos and c[i] > u[i]:
            sig[i], in_pos = 1.0, True
        elif in_pos and c[i] < l[i]:
            sig[i], in_pos = -1.0, False
    return pd.Series(sig)


def _sig_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    """动量策略：N 日涨幅（ROC）超过买入阈值开仓，跌破卖出阈值清仓"""
    n = int(p["n"])
    buy_th, sell_th = p["buy_th"] / 100.0, p["sell_th"] / 100.0
    roc = (df["close"] / df["close"].shift(n) - 1.0).tolist()
    sig, in_pos = [0.0] * len(roc), False
    for i, r in enumerate(roc):
        if r is None or pd.isna(r):
            continue
        if not in_pos and r > buy_th:
            sig[i], in_pos = 1.0, True
        elif in_pos and r < sell_th:
            sig[i], in_pos = -1.0, False
    return pd.Series(sig)


def _sig_grid(df: pd.DataFrame, p: dict) -> pd.Series:
    """等比网格：以首日收盘为锚，价格每跌一格买入一份、每涨一格卖出一份。
    信号值为比例：买入用当前现金的 m/(剩余可买份数)，卖出用持仓的 m/(持有份数)。
    适合震荡市；单边下跌会逐步买满网格（最多 levels 份）。"""
    grid = p["grid_pct"] / 100.0
    levels = int(p["levels"])
    closes = df["close"].tolist()
    sig = [0.0] * len(closes)
    anchor = closes[0]
    if anchor <= 0:
        return pd.Series(sig)
    ln_g = math.log(1.0 + grid)

    def _level(px):
        return int(math.floor(math.log(px / anchor) / ln_g))

    cur_k, bought, held = 0, 0, 0
    for i in range(1, len(closes)):
        k = _level(closes[i])
        if k < cur_k:                       # 跌穿新格 → 买（cur_k-k）份
            m = min(cur_k - k, levels - bought)
            if m > 0:
                ratio = m / (levels - bought)
                # 0.999 封顶：避免比例恰为 1.0 与"全仓信号仅空仓触发"的引擎规则冲突
                sig[i] = ratio if ratio < 1.0 else 0.999
                bought += m
                held += m
            cur_k = k
        elif k > cur_k:                     # 涨破新格 → 卖（k-cur_k）份
            m = min(k - cur_k, held)
            if m > 0:
                sig[i] = -(m / held)
                held -= m
            cur_k = k
    return pd.Series(sig)


# params 定义：(默认值, 下限, 上限)；默认值为 float 则参数按浮点清洗
STRATEGIES = {
    "ma_cross": {
        "label": "双均线交叉",
        "desc": "短均线上穿长均线（金叉）买入，下穿（死叉）卖出",
        "params": {"short": (5, 2, 60), "long": (20, 10, 250)},
        "signal": _sig_ma_cross,
    },
    "macd": {
        "label": "MACD金死叉",
        "desc": "DIF 上穿 DEA（金叉）买入，下穿（死叉）卖出；参数固定 12/26/9",
        "params": {},
        "signal": _sig_macd,
    },
    "kdj": {
        "label": "KDJ超买超卖",
        "desc": "J 值跌破 20（超卖）买入，升破 80（超买）卖出",
        "params": {},
        "signal": _sig_kdj,
    },
    "boll_revert": {
        "label": "布林带均值回归",
        "desc": "收盘跌破 N 日布林下轨（超卖）买入，回升至中轨上方卖出；K 为带宽标准差倍数",
        "params": {"n": (20, 10, 60), "k": (2.0, 0.5, 4.0)},
        "signal": _sig_boll_revert,
    },
    "donchian": {
        "label": "唐奇安通道突破",
        "desc": "突破前 N 日最高价买入（趋势入场），跌破前 M 日最低价卖出；海龟策略核心规则",
        "params": {"entry_n": (20, 5, 120), "exit_n": (10, 5, 120)},
        "signal": _sig_donchian,
    },
    "momentum": {
        "label": "动量策略",
        "desc": "N 日涨幅（ROC）超过买入阈值开仓，跌破卖出阈值清仓；追逐强势趋势",
        "params": {"n": (20, 5, 120), "buy_th": (5.0, -10.0, 30.0), "sell_th": (0.0, -30.0, 10.0)},
        "signal": _sig_momentum,
    },
    "grid": {
        "label": "网格交易",
        "desc": "以回测首日收盘为锚，每跌一格买入一份、每涨一格卖出一份（分仓）；适合震荡市",
        "params": {"grid_pct": (5.0, 1.0, 20.0), "levels": (8, 3, 20)},
        "signal": _sig_grid,
        "no_warmup": True,   # 无指标预热需求：锚定回测首日，信号仅在回测区间内计算
    },
}


def strategy_meta() -> list:
    """策略元数据（前端动态渲染参数表单用）"""
    return [
        {
            "key": key,
            "label": s["label"],
            "desc": s["desc"],
            "params": [
                {"name": name, "default": d, "min": lo, "max": hi}
                for name, (d, lo, hi) in s["params"].items()
            ],
        }
        for key, s in STRATEGIES.items()
    ]


def _norm_params(strategy: str, params: dict) -> dict:
    """参数清洗：白名单 + 按定义类型（int/float）转换并限幅 + 业务校验"""
    spec = STRATEGIES[strategy]["params"]
    out = {}
    for name, (d, lo, hi) in spec.items():
        cast = float if isinstance(d, float) else int
        try:
            v = cast(params.get(name, d))
        except (TypeError, ValueError):
            v = d
        out[name] = max(lo, min(hi, v))
    if "short" in out and "long" in out and out["short"] >= out["long"]:
        raise BacktestError("短期均线必须小于长期均线")
    if "entry_n" in out and "exit_n" in out and out["entry_n"] <= out["exit_n"]:
        raise BacktestError("入场突破周期应大于出场跌破周期（如 20/10）")
    return out


# ---------------------------------------------------------------- 数据加载
def _to_date(s) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s), "%Y-%m-%d").date()


def _load_stock_kline(code: str, start: date, end: date, warmup_days: int) -> list:
    """区间日K：本地优先 → 不足则 Tushare 拉取落库重读"""
    fetch_start = start - timedelta(days=warmup_days)
    rows = get_daily_kline(code, fetch_start, end)
    covered = [r for r in rows if _to_date(r["trade_date"]) >= start]
    # 本地已覆盖回测起点（允许 7 天节假日容差）才跳过补拉，否则补拉全区间
    if covered and _to_date(covered[0]["trade_date"]) <= start + timedelta(days=7):
        return rows
    try:  # 补拉全区间（REPLACE 幂等）
        df = ts_client.call(
            "daily", ts_code=code,
            start_date=fetch_start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            fields="trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
        )
        if df is not None and not df.empty:
            upsert_daily_kline(code, df)
            rows = get_daily_kline(code, fetch_start, end)
    except Exception:
        pass  # Tushare 冷却/限频时用本地已有数据（可能为空，上层报错）
    return rows


def _benchmark_series(dates: list) -> tuple:
    """基准归一化收益率（对齐回测交易日历，前向+后向填充）
    返回 (代码, {date_str: benchmark_pct})；沪深300 缺失降级上证指数"""
    start, end = _to_date(dates[0]), _to_date(dates[-1])
    for code in (BENCHMARK_CODE, BENCHMARK_FALLBACK):
        try:
            rows = get_index_kline(code, start, end)
            if len(rows) < len(dates) * 0.5:  # 覆盖不足 → Tushare 补拉落库
                df = ts_client.call(
                    "index_daily", ts_code=code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    fields="trade_date,open,high,low,close,pre_close,vol,amount",
                )
                if df is not None and not df.empty:
                    upsert_index_kline(code, df)
                    rows = get_index_kline(code, start, end)
        except Exception:
            rows = []
        if rows:
            s = pd.Series({str(r["trade_date"]): float(r["close"]) for r in rows})
            s = s.reindex(dates).ffill().bfill().dropna()
            if len(s) >= 2:
                base = float(s.iloc[0])
                return code, {d: round((float(v) / base - 1) * 100, 2)
                              for d, v in s.items()}
    return None, {}


# ---------------------------------------------------------------- 回测引擎
class BacktestService:

    @staticmethod
    def run(stock_code: str, strategy: str, params: dict,
            start_date: str, end_date: str, initial_capital: int = 100000) -> dict:
        """参数需为清洗后的合法值（API 层已校验）"""
        if strategy not in STRATEGIES:
            raise BacktestError("不支持的策略")
        norm = _norm_params(strategy, params or {})

        def _run():
            return _execute(stock_code, strategy, norm,
                            start_date, end_date, initial_capital)

        key = ("bt", stock_code, strategy, json.dumps(norm, sort_keys=True),
               start_date, end_date, initial_capital)
        return cached(key, _TTL, _run)

    @staticmethod
    def compare(stock_code: str, strategies: list, start_date: str, end_date: str,
                initial_capital: int = 100000, params_map: dict = None) -> list:
        """多策略横向对比：同一标的/区间逐策略回测，返回 {strategy, label, summary} 列表"""
        params_map = params_map or {}
        out = []
        for st in strategies:
            if st not in STRATEGIES:
                continue
            try:
                r = BacktestService.run(stock_code, st, params_map.get(st) or {},
                                        start_date, end_date, initial_capital)
            except BacktestError as e:
                out.append({"strategy": st, "label": STRATEGIES[st]["label"],
                            "summary": None, "error": str(e)})
                continue
            out.append({"strategy": st, "label": r["strategy_label"],
                        "summary": r["summary"]})
        return out

    @staticmethod
    def optimize(stock_code: str, strategy: str, start_date: str, end_date: str,
                 initial_capital: int = 100000, metric: str = "return_pct",
                 grid_points: int = 6) -> dict:
        """参数寻优：对策略前 2 个参数做网格搜索，返回 2D 指标矩阵（供热力图渲染）
        metric 见 _METRICS；非法组合（如 short>=long）自动剔除"""
        if strategy not in STRATEGIES:
            raise BacktestError("不支持的策略")
        if metric not in _METRICS:
            raise BacktestError("不支持的指标")
        items = list(STRATEGIES[strategy]["params"].items())
        if not items:
            raise BacktestError("该策略无参数（MACD/KDJ 参数固定），无法参数寻优")
        if len(items) == 1:
            raise BacktestError("该策略仅 1 个参数，暂不支持热力图寻优")

        x_name, (x_d, x_lo, x_hi) = items[0]
        y_name, (y_d, y_lo, y_hi) = items[1]

        def _grid(lo, hi, is_int):
            if is_int:
                lo, hi = int(lo), int(hi)
                span = hi - lo
                if span < 1:
                    return [lo]
                step = max(1, span // (grid_points - 1))
                vals = list(range(lo, hi + 1, step))[:grid_points]
                return vals
            return [round(lo + (hi - lo) * i / (grid_points - 1), 2)
                    for i in range(grid_points)]

        xs = _grid(x_lo, x_hi, isinstance(x_d, int))
        ys = _grid(y_lo, y_hi, isinstance(y_d, int))
        # 业务约束：均线 short<long、唐奇安 entry>exit
        def _valid(x, y):
            if x_name == "short" and y_name == "long" and x >= y:
                return False
            if x_name == "entry_n" and y_name == "exit_n" and x <= y:
                return False
            return True

        cells, best = [], None
        for x in xs:
            for y in ys:
                if not _valid(x, y):
                    continue
                params = {k: (v[0] if k not in (x_name, y_name) else None)
                          for k, v in STRATEGIES[strategy]["params"].items()}
                params[x_name], params[y_name] = x, y
                try:
                    r = BacktestService.run(stock_code, strategy, params,
                                            start_date, end_date, initial_capital)
                except BacktestError:
                    continue
                v = r["summary"].get(metric)
                if v is None:
                    continue
                cell = {"x": x, "y": y, "value": round(v, 2),
                        "trades": r["summary"].get("trade_count", 0)}
                cells.append(cell)
                if best is None or v > best["value"]:  # 回撤取最大=最浅，其余取最大=最优
                    best = {"x": x, "y": y, "value": round(v, 2)}

        if not cells:
            raise BacktestError("寻优区间内无有效组合（数据不足或参数区间不合法）")
        return {
            "strategy": strategy,
            "x_param": x_name, "y_param": y_name,
            "x_values": xs, "y_values": ys,
            "metric": metric,
            "cells": cells,
            "best": best,
        }


def _execute(stock_code: str, strategy: str, norm: dict,
             start_str: str, end_str: str, initial_capital: int) -> dict:
    from config import settings

    start, end = _to_date(start_str), _to_date(end_str)
    rules = settings.TRADE_RULES
    lot = int(rules["min_trade_num"])
    comm_rate, comm_min = float(rules["commission_rate"]), float(rules["commission_min"])
    tax_rate = float(rules["stamp_tax_rate"])

    # 指标预热：max(60, 3×最长周期参数) 个交易日（自然日 ×1.6 冗余）
    cycles = [v for v in norm.values() if isinstance(v, int)] or [26]
    warmup_trading = max(60, 3 * max(cycles))
    warmup_days = int(warmup_trading * 1.6) + 15

    rows = _load_stock_kline(stock_code, start, end, warmup_days)
    in_range = [r for r in rows if _to_date(r["trade_date"]) >= start]
    if len(in_range) < 10:
        raise BacktestError("区间行情数据不足（该股票可能未上市或数据暂不可用），请调整时间范围后重试")

    df = pd.DataFrame([{
        "date": str(r["trade_date"]),
        "open": float(r["open"] or 0),
        "high": float(r["high"] or 0),
        "low": float(r["low"] or 0),
        "close": float(r["close"] or 0),
    } for r in rows]).reset_index(drop=True)
    dates = df["date"].tolist()

    idx_start = dates.index(next(d for d in dates if d >= start_str))
    sig_src = df if not STRATEGIES[strategy].get("no_warmup") else df.iloc[idx_start:]
    sig = [0.0] * (len(df) - len(sig_src)) + STRATEGIES[strategy]["signal"](sig_src, norm).tolist()

    # ---------------- 逐日回放 ----------------
    # pending：次日开盘执行的信号强度（>=1 全仓买；0<s<1 按比例买；<0 按比例卖，<=-1 清仓）
    cash, qty = float(initial_capital), 0
    pending = 0.0
    avg_cost, last_buy_i = 0.0, -1     # 加权平均成本 / 最近买入日（T+1 判断）
    trades, points, wins, holds = [], [], 0, []

    def _buy(px, i, ratio=1.0):
        """按现金比例买入（ratio=1 全仓）；维护加权平均成本"""
        nonlocal cash, qty, avg_cost, last_buy_i
        n = int(cash * ratio / px / lot) * lot
        if n < lot:
            return
        amount = round(n * px, 2)
        fee = round(max(amount * comm_rate, comm_min), 2)
        cash -= amount + fee
        avg_cost = ((avg_cost * qty) + amount + fee) / (qty + n)
        qty += n
        last_buy_i = i
        trades.append({"date": dates[i], "side": "buy", "price": px,
                       "qty": n, "amount": amount, "fee": fee, "tax": 0.0})

    def _sell(px, i, ratio=1.0):
        """按持仓比例卖出（ratio=1 清仓），整手向下取整；对比平均成本计胜率"""
        nonlocal cash, qty, wins
        n = min(int(qty * ratio / lot) * lot, qty)
        if n < lot:
            return
        amount = round(n * px, 2)
        fee = round(max(amount * comm_rate, comm_min), 2)
        tax = round(amount * tax_rate, 2)
        cash += amount - fee - tax
        if amount - fee - tax > avg_cost * n:
            wins += 1
        holds.append(i - last_buy_i)
        trades.append({"date": dates[i], "side": "sell", "price": px,
                       "qty": n, "amount": amount, "fee": fee, "tax": tax})
        qty -= n

    for i in range(idx_start, len(df)):
        # ① 先执行昨日信号（今日开盘成交）
        if pending > 0 and (pending < 1 or qty == 0):     # 全仓信号仅空仓触发；比例信号随时
            _buy(float(df.loc[i, "open"]), i, ratio=min(1.0, pending))
        elif pending < 0 and qty > 0 and i - last_buy_i >= 1:  # T+1 兜底
            _sell(float(df.loc[i, "open"]), i, ratio=min(1.0, -pending))
        pending = 0.0
        # ② 收盘计算新信号
        if i < len(df) - 1:            # 最后一日无次日可执行
            s = sig[i]
            if s > 0 and (s < 1 or qty == 0):
                pending = s
            elif s < 0 and qty > 0:
                pending = s
        # ③ 收盘估值
        close_px = float(df.loc[i, "close"])
        asset = round(cash + qty * close_px, 2)
        points.append({"date": dates[i], "asset": asset,
                       "return_pct": round((asset / initial_capital - 1) * 100, 2)})

    # ---------------- 绩效 ----------------
    asset_s = pd.Series([p["asset"] for p in points])
    n_days = len(points)
    total_pct = round((float(asset_s.iloc[-1]) / initial_capital - 1) * 100, 2)
    ann_pct = round(((1 + total_pct / 100) ** (252 / n_days) - 1) * 100, 2) if n_days > 0 else 0.0
    cummax = asset_s.cummax()
    max_dd = round(float(((asset_s / cummax) - 1).min()) * 100, 2)
    daily_ret = asset_s.pct_change().dropna()
    sharpe = None
    if len(daily_ret) > 1 and float(daily_ret.std()) > 0:
        rf_daily = _RISK_FREE_ANNUAL / 252
        sharpe = round(float((daily_ret.mean() - rf_daily) / daily_ret.std() * math.sqrt(252)), 2)

    bench_code, bench_pct = _benchmark_series(dates[idx_start:])
    for p in points:
        p["benchmark_pct"] = bench_pct.get(p["date"])
    bench_final = points[-1]["benchmark_pct"] if points else None

    n_trades = len(trades)
    summary = {
        "start_date": points[0]["date"] if points else start_str,
        "end_date": points[-1]["date"] if points else end_str,
        "trading_days": n_days,
        "initial_capital": initial_capital,
        "final_asset": float(asset_s.iloc[-1]) if n_days else initial_capital,
        "return_pct": total_pct,
        "benchmark_pct": bench_final,
        "excess_pct": round(total_pct - bench_final, 2) if bench_final is not None else None,
        "annualized_pct": ann_pct,
        "max_drawdown_pct": max_dd,
        "sharpe": sharpe,
        "trade_count": n_trades,
        "win_rate_pct": round(wins / len(holds) * 100, 2) if holds else None,
        "avg_hold_days": round(sum(holds) / len(holds), 1) if holds else None,
        "open_position": qty > 0,      # 区间结束仍有持仓（按收盘估值）
    }

    return {
        "stock_code": stock_code,
        "strategy": strategy,
        "strategy_label": STRATEGIES[strategy]["label"],
        "params": norm,
        "benchmark": bench_code,
        "points": points,
        "trades": trades,
        "summary": summary,
    }
