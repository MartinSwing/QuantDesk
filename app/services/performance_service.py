# -*- coding: utf-8 -*-
"""
绩效服务：账户净值/收益率曲线（回放法）
- 以全市场 stock_daily 的交易日历为基准，逐日回放资金流水与成交订单
- 每日总资产 = 累计现金 + 持仓市值（按 stock_daily 收盘价，前向填充）
- 基准：沪深300（index_daily 缓存），归一化为同期收益率对比
"""
from bisect import bisect_left
from datetime import datetime, timedelta

import pandas as pd

from app.datasources.local_cache import upsert_daily_kline
from app.datasources.tushare_client import ts_client
from app.extensions import db

BENCHMARK_CODE = "000300.SH"   # 沪深300
BENCHMARK_FALLBACK = "000001.SH"  # 上证指数（沪深300缓存缺失时兜底）


def _trading_calendar(start, end) -> list:
    """交易日历：优先 Tushare trade_cal（免费接口），失败降级 stock_daily 缓存的去重日期"""
    try:
        df = ts_client.call(
            "trade_cal", exchange="SSE",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            fields="cal_date,is_open",
        )
        days = sorted(df[df["is_open"].astype(int) == 1]["cal_date"].astype(str))
        return [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in days]
    except Exception:
        rows = db.fetch_all(
            """SELECT DISTINCT trade_date FROM stock_daily
               WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date""",
            (start, end),
        )
        return [str(r["trade_date"]) for r in rows]


def _close_series(code: str, cal: list) -> pd.Series:
    """个股收盘价序列（对齐交易日历，前向填充）；缓存缺尾时增量补拉 Tushare"""
    rows = db.fetch_all(
        """SELECT trade_date, close FROM stock_daily
           WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date""",
        (code, cal[0], cal[-1]),
    )
    if rows and str(rows[-1]["trade_date"]) < cal[-1]:
        try:  # 缓存价格落后于日历，增量补拉缺失区间
            fetch_from = datetime.strptime(str(rows[-1]["trade_date"]), "%Y-%m-%d") + timedelta(days=1)
            df = ts_client.call(
                "daily", ts_code=code,
                start_date=fetch_from.strftime("%Y%m%d"),
                end_date=datetime.strptime(cal[-1], "%Y-%m-%d").strftime("%Y%m%d"),
                fields="trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
            )
            if df is not None and not df.empty:
                upsert_daily_kline(code, df)
                rows = db.fetch_all(
                    """SELECT trade_date, close FROM stock_daily
                       WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date""",
                    (code, cal[0], cal[-1]),
                )
        except Exception:
            pass  # Tushare 不可用时用现有缓存前向填充
    s = pd.Series({str(r["trade_date"]): float(r["close"]) for r in rows})
    return s.reindex(cal).ffill().fillna(0.0)


class PerformanceService:
    @staticmethod
    def equity_curve(user_account: str) -> dict:
        orders = db.fetch_all(
            """SELECT side, stock_code, quantity, amount, fee, tax, DATE(created_at) AS d
               FROM orders WHERE user_account = %s AND status = 'filled'
               ORDER BY created_at""",
            (user_account,),
        )
        flows = db.fetch_all(
            """SELECT flow_type, amount, DATE(created_at) AS d
               FROM capital_flows WHERE user_account = %s AND status = 'success'
               ORDER BY created_at""",
            (user_account,),
        )
        if not orders and not flows:
            return {"points": [], "benchmark": None, "summary": None}

        # ---------- 时间范围与交易日历 ----------
        event_dates = [r["d"] for r in flows] + [r["d"] for r in orders]
        start, end = min(event_dates), datetime.now().date()
        cal = _trading_calendar(start, end)
        if not cal:
            return {"points": [], "benchmark": None, "summary": None}

        def fold(d) -> str:
            """把事件日期折叠到其后的第一个交易日（晚于最后交易日的事件不入曲线）"""
            d = str(d)
            i = bisect_left(cal, d)
            return cal[i] if i < len(cal) else None

        # ---------- 现金曲线：入金 +，出金 -，买入 -(金额+佣金)，卖出 +(金额-费税) ----------
        cash_delta = pd.Series(0.0, index=cal)
        for f in flows:
            d = fold(f["d"])
            if d is None:
                continue
            amt = float(f["amount"])
            cash_delta[d] += amt if f["flow_type"] == "deposit" else -amt
        for o in orders:
            d = fold(o["d"])
            if d is None:
                continue
            if o["side"] == "buy":
                cash_delta[d] -= float(o["amount"]) + float(o["fee"])
            else:
                cash_delta[d] += float(o["amount"]) - float(o["fee"]) - float(o["tax"])
        cash = cash_delta.cumsum()

        # ---------- 持仓数量曲线（按日累计） ----------
        qty_curves = {}
        for o in orders:
            code = o["stock_code"]
            d = fold(o["d"])
            if d is None:
                continue
            if code not in qty_curves:
                qty_curves[code] = pd.Series(0.0, index=cal)
            qty_curves[code][d] += float(o["quantity"]) if o["side"] == "buy" else -float(o["quantity"])

        # ---------- 持仓市值：数量 × 收盘价（前向填充） ----------
        market_value = pd.Series(0.0, index=cal)
        for code, qty in qty_curves.items():
            if (qty.cumsum() <= 0).all():
                continue  # 纯卖出/无净持仓的数据异常，跳过估值
            price = _close_series(code, cal)
            market_value = market_value.add(qty.cumsum() * price, fill_value=0.0)

        asset = (cash + market_value).round(2)

        # ---------- 基准：沪深300 归一化收益率 ----------
        bench_points, bench_code = None, None
        for code in (BENCHMARK_CODE, BENCHMARK_FALLBACK):
            rows = db.fetch_all(
                """SELECT trade_date, close FROM index_daily
                   WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date""",
                (code, cal[0], cal[-1]),
            )
            if rows:
                s = pd.Series({str(r["trade_date"]): float(r["close"]) for r in rows})
                s = s.reindex(cal).ffill().dropna()
                if len(s) >= 2:
                    base = float(s.iloc[0])
                    bench_points = {d: round((float(v) / base - 1) * 100, 2)
                                    for d, v in s.items()}
                    bench_code = code
                    break

        # ---------- 组装输出 ----------
        initial = float(asset.iloc[0])
        points = []
        for d, v in asset.items():
            ret = round((float(v) / initial - 1) * 100, 2) if initial else 0.0
            points.append({
                "date": d,
                "asset": float(v),
                "return_pct": ret,
                "benchmark_pct": bench_points.get(d) if bench_points else None,
            })

        # 最大回撤（按总资产）
        cummax = asset.cummax()
        max_dd = round(float(((asset / cummax) - 1).min()) * 100, 2)
        bench_ret = None
        if bench_points:
            vals = [v for v in bench_points.values() if v is not None]
            bench_ret = vals[-1] if vals else None

        summary = {
            "start_date": cal[0],
            "end_date": cal[-1],
            "initial_asset": initial,
            "latest_asset": float(asset.iloc[-1]),
            "return_pct": points[-1]["return_pct"],
            "benchmark_pct": bench_ret,
            "excess_pct": round(points[-1]["return_pct"] - bench_ret, 2) if bench_ret is not None else None,
            "max_drawdown_pct": max_dd,
        }
        return {"points": points, "benchmark": bench_code, "summary": summary}
