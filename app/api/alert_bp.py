# -*- coding: utf-8 -*-
"""价格提醒接口"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.services.alert_service import AlertService

alert_bp = Blueprint("alert", __name__, url_prefix="/api/alert")


@alert_bp.route("/add", methods=["POST"])
@login_required
def add():
    d = request.get_json(silent=True) or {}
    code = (d.get("stock_code") or "").strip()
    if not code:
        return jsonify({"code": -1, "msg": "请传入股票代码"})
    ok, msg = AlertService.add(current_user.user_account, code,
                               d.get("condition", "gte"), d.get("target_price"))
    return jsonify({"code": 0 if ok else -1, "msg": msg})


@alert_bp.route("/cancel", methods=["POST"])
@login_required
def cancel():
    d = request.get_json(silent=True) or {}
    try:
        alert_id = int(d.get("id"))
    except (TypeError, ValueError):
        return jsonify({"code": -1, "msg": "id 不合法"})
    ok, msg = AlertService.cancel(current_user.user_account, alert_id)
    return jsonify({"code": 0 if ok else -1, "msg": msg})


@alert_bp.route("/list")
@login_required
def listing():
    return jsonify({"code": 0, "msg": "成功", "data": AlertService.list_user(current_user.user_account)})


@alert_bp.route("/check")
@login_required
def check():
    """惰性检查：前端页面加载时调用，返回本次触发的新提醒"""
    return jsonify({"code": 0, "msg": "成功", "data": AlertService.check_user(current_user.user_account)})
