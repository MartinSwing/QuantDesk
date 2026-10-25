# -*- coding: utf-8 -*-
"""管理员接口：用户管理（仅 admin 角色）"""
from functools import wraps

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.models.user_dao import UserDAO

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({"code": -1, "msg": "需要管理员权限", "data": {}})
        return fn(*args, **kwargs)
    return wrapper


@admin_bp.route("/users")
@admin_required
def users():
    return jsonify({"code": 0, "msg": "成功", "data": UserDAO.list_all()})


@admin_bp.route("/user/status", methods=["POST"])
@admin_required
def set_status():
    data = request.get_json(silent=True) or {}
    account = (data.get("user_account") or "").strip()
    status = data.get("status")
    if not account or status not in ("active", "disabled"):
        return jsonify({"code": -1, "msg": "参数不正确", "data": {}})
    if account == current_user.user_account:
        return jsonify({"code": -1, "msg": "不能操作自己的账号", "data": {}})
    UserDAO.set_status(account, status)
    return jsonify({"code": 0, "msg": f"已{'启用' if status == 'active' else '禁用'} {account}", "data": {}})


@admin_bp.route("/user/role", methods=["POST"])
@admin_required
def set_role():
    data = request.get_json(silent=True) or {}
    account = (data.get("user_account") or "").strip()
    role = data.get("role")
    if not account or role not in ("user", "admin"):
        return jsonify({"code": -1, "msg": "参数不正确", "data": {}})
    if account == current_user.user_account:
        return jsonify({"code": -1, "msg": "不能操作自己的账号", "data": {}})
    UserDAO.set_role(account, role)
    return jsonify({"code": 0, "msg": f"已将 {account} 设为 {role}", "data": {}})


@admin_bp.route("/user/reset_password", methods=["POST"])
@admin_required
def reset_password():
    data = request.get_json(silent=True) or {}
    account = (data.get("user_account") or "").strip()
    new_pwd = (data.get("new_password") or "").strip()
    if not account or len(new_pwd) < 6:
        return jsonify({"code": -1, "msg": "新密码至少6位", "data": {}})
    if not UserDAO.get_by_account(account):
        return jsonify({"code": -1, "msg": "账号不存在", "data": {}})
    UserDAO.update_password(account, new_pwd)
    return jsonify({"code": 0, "msg": f"已重置 {account} 的密码", "data": {}})
