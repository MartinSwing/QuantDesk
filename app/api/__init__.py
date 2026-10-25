# -*- coding: utf-8 -*-
"""接口层：按业务域拆分 Blueprint"""
from app.api.auth_bp import auth_bp
from app.api.market_bp import market_bp
from app.api.trade_bp import trade_bp
from app.api.asset_bp import asset_bp
from app.api.favorite_bp import favorite_bp
from app.api.alert_bp import alert_bp
from app.api.admin_bp import admin_bp
from app.api.backtest_bp import backtest_bp

BLUEPRINTS = [auth_bp, market_bp, trade_bp, asset_bp, favorite_bp, alert_bp, admin_bp, backtest_bp]


def register_blueprints(app):
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
