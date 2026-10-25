# -*- coding: utf-8 -*-
"""交易接口：买入 / 卖出 / 成交记录"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.models.trade_dao import OrderDAO
from app.services.trading_service import TradeError, TradingService

trade_bp = Blueprint("trade", __name__, url_prefix="/api/trade")


@trade_bp.route("/buy", methods=["POST"])
@login_required
def buy():
    data = request.get_json(silent=True) or {}
    stock_code = (data.get("stock_code") or "").strip()
    quantity = data.get("quantity")
    price = data.get("price")            # 可选：弹窗自定义成交价
    trade_time = data.get("trade_time")  # 可选：弹窗自定义成交时间
    if not stock_code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    try:
        result = TradingService.buy(current_user.user_account, stock_code, quantity,
                                    price=price, trade_time=trade_time)
        return jsonify({"code": 0, "msg": "买入成功", "data": result})
    except TradeError as e:
        return jsonify({"code": -1, "msg": str(e), "data": {}})
    except Exception as e:
        return jsonify({"code": -1, "msg": f"系统异常：{str(e)[:80]}", "data": {}})


@trade_bp.route("/sell", methods=["POST"])
@login_required
def sell():
    data = request.get_json(silent=True) or {}
    stock_code = (data.get("stock_code") or "").strip()
    quantity = data.get("quantity")
    price = data.get("price")
    trade_time = data.get("trade_time")
    if not stock_code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    try:
        result = TradingService.sell(current_user.user_account, stock_code, quantity,
                                     price=price, trade_time=trade_time)
        return jsonify({"code": 0, "msg": "卖出成功", "data": result})
    except TradeError as e:
        return jsonify({"code": -1, "msg": str(e), "data": {}})
    except Exception as e:
        return jsonify({"code": -1, "msg": f"系统异常：{str(e)[:80]}", "data": {}})


@trade_bp.route("/records")
@login_required
def records():
    """成交记录（服务端分页）"""
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(max(1, int(request.args.get("page_size", 20))), 100)
    except (TypeError, ValueError):
        page, page_size = 1, 20
    account = current_user.user_account
    total = OrderDAO.count_by_user(account)
    rows = OrderDAO.list_by_user_paged(account, page=page, page_size=page_size)
    pages = max(1, (total + page_size - 1) // page_size)
    return jsonify({"code": 0, "msg": "成功", "data": {
        "total": total, "page": page, "page_size": page_size, "pages": pages,
        "rows": [{
            "order_no": r["order_no"],
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "side": r["side"],
            "price": float(r["price"]),
            "quantity": r["quantity"],
            "amount": float(r["amount"]),
            "fee": float(r["fee"]),
            "tax": float(r["tax"]),
            "status": r["status"],
            "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
        } for r in rows]}})
