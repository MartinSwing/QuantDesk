# -*- coding: utf-8 -*-
"""认证：登录 / 注册 / 找回密码 / 登出"""
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import login_manager
from app.models.user_dao import ProfileDAO, User, UserDAO

auth_bp = Blueprint("auth", __name__)

PROVINCES = [
    "北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省",
    "黑龙江省", "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省", "重庆市",
    "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省", "青海省",
    "宁夏回族自治区", "新疆维吾尔自治区", "香港特别行政区", "澳门特别行政区", "台湾省",
]

# ---------- 登录/找回密码限流（进程内计数，防暴力破解） ----------
_login_fails = defaultdict(list)    # account -> [失败时间戳]
_fp_fails = defaultdict(list)       # ip -> [失败时间戳]
_LOGIN_LOCK_N, _LOGIN_LOCK_MIN = 5, 15
_FP_LOCK_N, _FP_LOCK_SEC = 5, 600


def _is_locked(bucket, key, threshold, window_sec):
    cutoff = time.time() - window_sec
    bucket[key] = [t for t in bucket[key] if t > cutoff]
    return len(bucket[key]) >= threshold


def _valid_password(pwd: str) -> bool:
    """≥8 位且含字母和数字"""
    return len(pwd) >= 8 and bool(re.search(r"[A-Za-z]", pwd)) and bool(re.search(r"\d", pwd))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("page.index"))
    if request.method == "POST":
        account = request.form.get("user_account", "").strip()
        password = request.form.get("password", "")
        if _is_locked(_login_fails, account, _LOGIN_LOCK_N, _LOGIN_LOCK_MIN * 60):
            flash(f"尝试次数过多，请 {_LOGIN_LOCK_MIN} 分钟后再试", "error")
            return render_template("login.html")
        row = UserDAO.verify(account, password)
        if not row:
            _login_fails[account].append(time.time())
            flash("账号或密码错误！", "error")
            return render_template("login.html")
        if row["status"] != "active":
            flash("该账号已被禁用，请联系管理员", "error")
            return render_template("login.html")
        _login_fails.pop(account, None)   # 登录成功清零
        login_user(User(row), remember=True)
        flash(f"登录成功，欢迎 {account}", "success")
        return redirect(url_for("page.index"))
    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        data = {
            "real_name": request.form.get("real_name", "").strip(),
            "id_card": request.form.get("id_card", "").strip(),
            "birthday": request.form.get("birthday", ""),
            "phone": request.form.get("phone", "").strip(),
            "bank_card": request.form.get("bank_card", "").strip(),
            "province": request.form.get("province", ""),
            "occupation": request.form.get("occupation", "").strip(),
            "address": request.form.get("address", "").strip(),
        }
        # 校验
        errors = []
        if len(password) < 6:
            errors.append("密码至少6位")
        if password != confirm:
            errors.append("两次输入的密码不一致")
        if not data["real_name"]:
            errors.append("请填写真实姓名")
        if len(data["id_card"]) != 18 or not data["id_card"][:17].isdigit():
            errors.append("身份证号格式不正确")
        if len(data["phone"]) != 11 or not data["phone"].isdigit():
            errors.append("手机号格式不正确")
        if not data["bank_card"].isdigit():
            errors.append("银行卡号必须为纯数字")
        if not data["province"]:
            errors.append("请选择省份")
        if errors:
            flash("；".join(errors), "error")
            return render_template("register.html", provinces=PROVINCES, form=data)
        if ProfileDAO.find_by_identity(data["real_name"], data["id_card"], data["phone"]):
            flash("该身份信息已注册过账号！", "error")
            return render_template("register.html", provinces=PROVINCES, form=data)

        try:
            account = UserDAO.create(password)
            ProfileDAO.create(account, data)
        except Exception as e:
            msg = str(e)
            if "uk_id_card" in msg:
                flash("该身份证号已被使用", "error")
            elif "uk_phone" in msg:
                flash("该手机号已被使用", "error")
            else:
                flash(f"注册失败：{msg[:80]}", "error")
            return render_template("register.html", provinces=PROVINCES, form=data)

        flash(f"注册成功！您的账号为 {account}，请牢记！", "success")
        return redirect(url_for("auth.login"))
    return render_template("register.html", provinces=PROVINCES, form={})


@auth_bp.route("/forget_pwd", methods=["GET", "POST"])
def forget_pwd():
    """两步找回：先身份验证，后重置密码。
    安全加固：① step1 限流；② 验证通过签发短时会话令牌（session），
    step2 必须凭令牌改密，无法绕过 step1 直接改任意账号密码。"""
    if request.method == "POST":
        step = request.form.get("step", "1")
        if step == "1":
            ip = request.remote_addr or "unknown"
            if _is_locked(_fp_fails, ip, _FP_LOCK_N, _FP_LOCK_SEC):
                flash("尝试次数过多，请稍后再试", "error")
                return render_template("forget_pwd.html", step="1")
            row = ProfileDAO.find_by_identity(
                request.form.get("real_name", "").strip(),
                request.form.get("id_card", "").strip(),
                request.form.get("phone", "").strip(),
            )
            if not row:
                _fp_fails[ip].append(time.time())
                flash("身份信息验证失败，请检查姓名/身份证/手机号", "error")
                return render_template("forget_pwd.html", step="1")
            # 签发短时会话令牌（签名 session 可信），绑定账号，5 分钟有效
            session["pwd_reset"] = {"account": row["user_account"],
                                    "expires": time.time() + 300}
            flash("身份验证通过，请设置新密码（5 分钟内有效）", "success")
            return render_template("forget_pwd.html", step="2",
                                   user_account=row["user_account"])
        # step 2：必须携带有效令牌
        account = request.form.get("user_account", "")
        new_pwd = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        reset = session.get("pwd_reset")
        if (not reset or reset.get("account") != account
                or time.time() > reset.get("expires", 0)):
            session.pop("pwd_reset", None)
            flash("重置会话已过期，请重新验证身份", "error")
            return render_template("forget_pwd.html", step="1")
        if not _valid_password(new_pwd):
            flash("密码至少 8 位且包含字母和数字", "error")
            return render_template("forget_pwd.html", step="2", user_account=account)
        if new_pwd != confirm:
            flash("两次输入的密码不一致", "error")
            return render_template("forget_pwd.html", step="2", user_account=account)
        UserDAO.update_password(account, new_pwd)
        session.pop("pwd_reset", None)
        flash("密码重置成功，请使用新密码登录", "success")
        return redirect(url_for("auth.login"))
    return render_template("forget_pwd.html", step="1")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("已安全退出", "success")
    return redirect(url_for("auth.login"))
