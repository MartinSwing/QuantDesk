# -*- coding: utf-8 -*-
"""资产接口：资产汇总 / 出入金 / 流水"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.services.asset_service import AssetError, AssetService

asset_bp = Blueprint("asset", __name__, url_prefix="/api/asset")


@asset_bp.route("/summary")
@login_required
def summary():
    return jsonify({"code": 0, "msg": "成功",
                    "data": AssetService.summary(current_user.user_account)})


@asset_bp.route("/deposit", methods=["POST"])
@login_required
def deposit():
    data = request.get_json(silent=True) or {}
    try:
        result = AssetService.deposit(current_user.user_account, data.get("amount"),
                                      channel=(data.get("channel") or "").strip(),
                                      remark=(data.get("remark") or "").strip())
        return jsonify({"code": 0, "msg": f"入金成功：{result['amount']} 元", "data": result})
    except AssetError as e:
        return jsonify({"code": -1, "msg": str(e), "data": {}})


@asset_bp.route("/withdraw", methods=["POST"])
@login_required
def withdraw():
    data = request.get_json(silent=True) or {}
    try:
        result = AssetService.withdraw(current_user.user_account, data.get("amount"),
                                       channel=(data.get("channel") or "").strip(),
                                       remark=(data.get("remark") or "").strip())
        return jsonify({"code": 0, "msg": f"出金成功：{result['amount']} 元", "data": result})
    except AssetError as e:
        return jsonify({"code": -1, "msg": str(e), "data": {}})


@asset_bp.route("/flows")
@login_required
def flows():
    return jsonify({"code": 0, "msg": "成功",
                    "data": AssetService.flows(current_user.user_account)})


@asset_bp.route("/equity_curve")
@login_required
def equity_curve():
    """账户净值/收益率曲线（回放计算，含沪深300基准对比）"""
    from app.services.performance_service import PerformanceService
    try:
        data = PerformanceService.equity_curve(current_user.user_account)
        return jsonify({"code": 0, "msg": "成功", "data": data})
    except Exception as e:
        return jsonify({"code": -1, "msg": f"收益曲线计算失败：{str(e)[:80]}",
                        "data": {"points": [], "benchmark": None, "summary": None}})
