# -*- coding: utf-8 -*-
"""交易服务：买入/卖出（含佣金、印花税、资金与持仓的事务一致性）"""
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.extensions import db
from app.models.trade_dao import CapitalFlowDAO, OrderDAO, PositionDAO
from app.services.market_service import MarketService
from config import settings


class TradeError(Exception):
    """业务异常：消息可直接展示给用户"""


def _d(value) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class TradingService:

    @staticmethod
    def _fee(amount: Decimal) -> tuple:
        """返回 (佣金, 印花税)。佣金双边收取，印花税仅卖出"""
        rules = settings.TRADE_RULES
        fee = max(amount * _d(rules["commission_rate"]), _d(rules["commission_min"]))
        return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), Decimal("0")

    @staticmethod
    def _validate_quantity(quantity) -> int:
        rules = settings.TRADE_RULES
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise TradeError("交易数量必须为数字")
        if quantity <= 0:
            raise TradeError("交易数量必须大于0")
        if quantity % rules["min_trade_num"] != 0:
            raise TradeError(f"交易数量必须为{rules['min_trade_num']}股的整数倍（一手）")
        return quantity

    @staticmethod
    def _validate_price(price) -> Decimal:
        """用户在弹窗中自定义的成交价"""
        try:
            price = _d(price)
        except Exception:
            raise TradeError("股价格式不正确")
        if price <= 0:
            raise TradeError("股价必须大于0")
        if price > 100000:
            raise TradeError("股价数值异常，请检查后重试")
        return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _validate_trade_time(trade_time) -> datetime | None:
        """成交时间：不可为未来时间；为空则由数据库记录服务器时间"""
        if trade_time in (None, ""):
            return None
        if isinstance(trade_time, datetime):
            dt = trade_time
        else:
            s = str(trade_time).strip().replace("T", " ")
            dt = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                raise TradeError("交易时间格式不正确")
        if dt > datetime.now() + timedelta(minutes=1):
            raise TradeError("交易时间不可为未来时间")
        return dt

    @staticmethod
    def buy(user_account: str, stock_code: str, quantity, price=None, trade_time=None) -> dict:
        quantity = TradingService._validate_quantity(quantity)
        trade_time = TradingService._validate_trade_time(trade_time)
        stock_name = MarketService.get_stock_name(stock_code)
        if price is None:
            price = MarketService.get_latest_price(stock_code)
            if not price or price <= 0:
                raise TradeError("暂无该股票行情数据，无法交易，请稍后再试")
        price = TradingService._validate_price(price)

        amount = _d(price) * quantity
        fee, _ = TradingService._fee(amount)
        total = amount + fee

        t_plus_1 = settings.TRADE_RULES["t_plus_1"]
        with db.transaction() as cur:
            cur.execute(
                "SELECT available_balance FROM accounts WHERE user_account=%s FOR UPDATE",
                (user_account,),
            )
            account = cur.fetchone()
            if not account or _d(account["available_balance"]) < total:
                raise TradeError(f"可用资金不足，本次需 {_money(total)} 元（含佣金 {_money(fee)} 元）")

            cur.execute(
                "UPDATE accounts SET available_balance = available_balance - %s WHERE user_account=%s",
                (str(_money(total)), user_account),
            )
            order = {
                "order_no": OrderDAO.gen_order_no(),
                "user_account": user_account,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "side": "buy",
                "price": price,
                "quantity": quantity,
                "amount": _money(amount),
                "fee": _money(fee),
                "tax": 0.0,
                "status": "filled",
                "remark": "买入成交",
                "created_at": trade_time,
            }
            OrderDAO.insert(cur, order)
            PositionDAO.upsert_after_buy(cur, user_account, stock_code, stock_name,
                                         quantity, float(price), t_plus_1)
        return {"order_no": order["order_no"], "price": _money(price), "quantity": quantity,
                "amount": _money(amount), "fee": _money(fee),
                "total": _money(total), "stock_name": stock_name}

    @staticmethod
    def sell(user_account: str, stock_code: str, quantity, price=None, trade_time=None) -> dict:
        quantity = TradingService._validate_quantity(quantity)
        trade_time = TradingService._validate_trade_time(trade_time)
        stock_name = MarketService.get_stock_name(stock_code)
        if price is None:
            price = MarketService.get_latest_price(stock_code)
            if not price or price <= 0:
                raise TradeError("暂无该股票行情数据，无法交易，请稍后再试")
        price = TradingService._validate_price(price)

        amount = _d(price) * quantity
        fee, tax = TradingService._fee(amount)
        tax = (amount * _d(settings.TRADE_RULES["stamp_tax_rate"])).quantize(Decimal("0.01"))
        proceeds = amount - fee - tax

        with db.transaction() as cur:
            cur.execute(
                "SELECT quantity, available_quantity FROM positions "
                "WHERE user_account=%s AND stock_code=%s FOR UPDATE",
                (user_account, stock_code),
            )
            position = cur.fetchone()
            if not position or position["quantity"] < quantity:
                raise TradeError("持仓数量不足")
            if position["available_quantity"] < quantity:
                raise TradeError("可卖数量不足（当日买入的股票次一交易日方可卖出）")

            cur.execute(
                "UPDATE accounts SET available_balance = available_balance + %s WHERE user_account=%s",
                (str(_money(proceeds)), user_account),
            )
            order = {
                "order_no": OrderDAO.gen_order_no(),
                "user_account": user_account,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "side": "sell",
                "price": price,
                "quantity": quantity,
                "amount": _money(amount),
                "fee": _money(fee),
                "tax": float(tax),
                "status": "filled",
                "remark": "卖出成交",
                "created_at": trade_time,
            }
            OrderDAO.insert(cur, order)
            PositionDAO.reduce_after_sell(cur, user_account, stock_code, quantity)
        return {"order_no": order["order_no"], "price": _money(price), "quantity": quantity,
                "amount": _money(amount), "fee": _money(fee), "tax": float(tax),
                "proceeds": _money(proceeds), "stock_name": stock_name}
