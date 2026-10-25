# -*- coding: utf-8 -*-
"""QuantDesk 应用工厂"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from config import settings


def _setup_logging(app: Flask):
    """统一日志：控制台 + logs/app.log 滚动文件；同时兜底未捕获异常"""
    log_dir = Path(app.root_path).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        root.addHandler(stream)
    file_handler = RotatingFileHandler(log_dir / "app.log",
                                       maxBytes=5 * 1024 * 1024,
                                       backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder="templates",
                static_folder="static")
    _setup_logging(app)
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["JSON_AS_ASCII"] = False

    # 初始化扩展
    from app.extensions import login_manager
    login_manager.init_app(app)

    # 注册蓝图
    from app.api import register_blueprints
    register_blueprints(app)
    from app.views.page_bp import page_bp
    app.register_blueprint(page_bp)

    # 统一错误响应：/api/* 返回JSON，页面路径返回错误页
    def _is_api():
        return request.path.startswith("/api/")

    @app.errorhandler(404)
    def not_found(e):
        if _is_api():
            return jsonify({"code": -1, "msg": "接口不存在", "data": {}}), 404
        return render_template("error.html", msg="页面不存在（404）"), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("未捕获异常: %s", request.path)
        if _is_api():
            return jsonify({"code": -1, "msg": "服务器内部错误", "data": {}}), 500
        return render_template("error.html", msg="服务器内部错误（500）"), 500

    return app
