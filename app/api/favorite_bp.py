# -*- coding: utf-8 -*-
"""自选股接口"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.models.favorite_dao import WatchlistDAO
from app.services.market_service import MarketService

favorite_bp = Blueprint("favorite", __name__, url_prefix="/api/favorite")


@favorite_bp.route("/list")
@login_required
def list_favorites():
    items = WatchlistDAO.list_by_user(current_user.user_account)
    codes = [i["stock_code"] for i in items]
    quotes = {q["stock_code"]: q for q in MarketService.get_realtime_quotes(codes)} if codes else {}
    data = []
    for i in items:
        q = quotes.get(i["stock_code"], {})
        data.append({
            "stock_code": i["stock_code"],
            "stock_name": i["stock_name"] or q.get("stock_name", i["stock_code"]),
            "current_price": q.get("current_price"),
            "pct_change": q.get("pct_change", 0.0),
            "high": q.get("high"),
            "low": q.get("low"),
            "source": q.get("source", "none"),
        })
    return jsonify({"code": 0, "msg": "成功", "data": data})


@favorite_bp.route("/add", methods=["POST"])
@login_required
def add():
    data = request.get_json(silent=True) or {}
    code = (data.get("stock_code") or "").strip()
    if not code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    name = data.get("stock_name") or MarketService.get_stock_name(code)
    if WatchlistDAO.add(current_user.user_account, code, name):
        return jsonify({"code": 0, "msg": f"{name}({code}) 已加入自选", "data": {}})
    return jsonify({"code": -1, "msg": "该股票已在自选中", "data": {}})


@favorite_bp.route("/remove", methods=["POST"])
@login_required
def remove():
    data = request.get_json(silent=True) or {}
    code = (data.get("stock_code") or "").strip()
    if not code:
        return jsonify({"code": -1, "msg": "请传入股票代码", "data": {}})
    if WatchlistDAO.remove(current_user.user_account, code):
        return jsonify({"code": 0, "msg": "已从自选移除", "data": {}})
    return jsonify({"code": -1, "msg": "该股票不在自选中", "data": {}})
