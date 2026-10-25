# -*- coding: utf-8 -*-
"""资产服务：资产汇总（含持仓市值与盈亏实时计算）、资金出入"""
from decimal import Decimal

from app.extensions import db
from app.models.asset_dao import AccountDAO
from app.models.trade_dao import CapitalFlowDAO, PositionDAO
from app.services.market_service import MarketService
from config import settings


class AssetError(Exception):
    pass


class AssetService:

    @staticmethod
    def summary(user_account: str) -> dict:
        AccountDAO.ensure(user_account)
        account = AccountDAO.get(user_account)
        balance = float(account["available_balance"]) if account else 0.0
        frozen = float(account["frozen_balance"]) if account else 0.0

        positions = PositionDAO.list_by_user(user_account)
        codes = [p["stock_code"] for p in positions]
        quotes = {q["stock_code"]: q for q in MarketService.get_realtime_quotes(codes)} if codes else {}

        hold_list = []
        total_market_value = 0.0
        total_profit = 0.0
        for p in positions:
            qty = p["quantity"]
            avg_cost = float(p["avg_cost"])
            quote = quotes.get(p["stock_code"])
            price = quote["current_price"] if quote and quote["current_price"] else avg_cost
            market_value = round(price * qty, 2)
            profit = round((price - avg_cost) * qty, 2)
            profit_rate = round(profit / (avg_cost * qty) * 100, 2) if avg_cost > 0 and qty > 0 else 0.0
            total_market_value += market_value
            total_profit += profit
            hold_list.append({
                "stock_code": p["stock_code"],
                "stock_name": p["stock_name"] or p["stock_code"],
                "quantity": qty,
                "available_quantity": p["available_quantity"],
                "avg_cost": avg_cost,
                "current_price": price,
                "market_value": market_value,
                "profit_loss": profit,
                "profit_loss_rate": profit_rate,
            })

        total_asset = round(balance + total_market_value, 2)

        return {
            "available_balance": round(balance, 2),
            "frozen_balance": round(frozen, 2),
            "market_value": round(total_market_value, 2),
            "total_asset": total_asset,
            "total_profit": round(total_profit, 2),
            "hold_list": hold_list,
        }

    @staticmethod
    def deposit(user_account: str, amount, channel: str = "", remark: str = "") -> dict:
        try:
            amount = Decimal(str(amount))
        except Exception:
            raise AssetError("金额格式不正确")
        if amount <= 0:
            raise AssetError("入金金额必须大于0")
        if amount > 10000000:
            raise AssetError("单笔入金不可超过1000万")

        flow_no = CapitalFlowDAO.gen_flow_no()
        with db.transaction() as cur:
            cur.execute(
                "UPDATE accounts SET available_balance = available_balance + %s WHERE user_account=%s",
                (str(amount), user_account),
            )
            CapitalFlowDAO.insert(cur, {
                "flow_no": flow_no, "user_account": user_account,
                "flow_type": "deposit", "amount": float(amount), "status": "success",
                "channel": channel[:20], "remark": remark[:100],
            })
        return {"flow_no": flow_no, "amount": float(amount)}

    @staticmethod
    def withdraw(user_account: str, amount, channel: str = "", remark: str = "") -> dict:
        try:
            amount = Decimal(str(amount))
        except Exception:
            raise AssetError("金额格式不正确")
        if amount <= 0:
            raise AssetError("出金金额必须大于0")

        flow_no = CapitalFlowDAO.gen_flow_no()
        with db.transaction() as cur:
            cur.execute(
                "SELECT available_balance FROM accounts WHERE user_account=%s FOR UPDATE",
                (user_account,),
            )
            account = cur.fetchone()
            if not account or Decimal(str(account["available_balance"])) < amount:
                raise AssetError("可用资金不足")
            cur.execute(
                "UPDATE accounts SET available_balance = available_balance - %s WHERE user_account=%s",
                (str(amount), user_account),
            )
            CapitalFlowDAO.insert(cur, {
                "flow_no": flow_no, "user_account": user_account,
                "flow_type": "withdraw", "amount": float(amount), "status": "success",
                "channel": channel[:20], "remark": remark[:100],
            })
        return {"flow_no": flow_no, "amount": float(amount)}

    @staticmethod
    def flows(user_account: str) -> list:
        rows = CapitalFlowDAO.list_by_user(user_account)
        return [{
            "flow_no": r["flow_no"],
            "flow_type": "入金" if r["flow_type"] == "deposit" else "出金",
            "amount": float(r["amount"]),
            "channel": r.get("channel") or "-",
            "remark": r.get("remark") or "",
            "status": r["status"],
            "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
        } for r in rows]
