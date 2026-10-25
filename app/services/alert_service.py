# -*- coding: utf-8 -*-
"""
价格提醒服务
- 用户对自选股设置"涨到X / 跌到X"提醒，存 price_alert 表
- check_user_alerts：取该用户所有 active 提醒 → 批量查最新价（复用 MarketService 实时行情）
  → 满足条件的标记 triggered 并返回；由前端（自选股页/首页加载时）轮询触发
- 说明：单进程模拟盘采用"访问时惰性检查"而非后台常驻定时任务，部署简单且够用
"""
from app.extensions import db
from app.services.market_service import MarketService


class AlertService:

    @staticmethod
    def add(user_account: str, stock_code: str, cond: str, target_price: float) -> tuple:
        if cond not in ("gte", "lte"):
            return False, "提醒条件只能是 涨到(gte) 或 跌到(lte)"
        try:
            price = round(float(target_price), 3)
        except (TypeError, ValueError):
            return False, "目标价格不合法"
        if price <= 0:
            return False, "目标价格必须大于0"

        name = MarketService.get_stock_name(stock_code)
        db.execute(
            "INSERT INTO price_alert (user_account, stock_code, stock_name, cond, target_price) "
            "VALUES (%s, %s, %s, %s, %s)",
            (user_account, stock_code, name, cond, price))
        return True, f"已设置提醒：{name or stock_code} {'涨到' if cond == 'gte' else '跌到'} {price}"

    @staticmethod
    def cancel(user_account: str, alert_id: int) -> tuple:
        n = db.execute(
            "UPDATE price_alert SET status='cancelled' "
            "WHERE id=%s AND user_account=%s AND status='active'",
            (alert_id, user_account))
        return (n > 0), "已取消" if n > 0 else "提醒不存在或已处理"

    @staticmethod
    def list_user(user_account: str) -> list:
        return db.fetch_all(
            "SELECT id, stock_code, stock_name, cond, target_price, status, created_at, triggered_at "
            "FROM price_alert WHERE user_account=%s ORDER BY id DESC LIMIT 50",
            (user_account,))

    @staticmethod
    def check_user(user_account: str) -> list:
        """惰性检查：把该用户 active 提醒与最新行情比对，满足即触发；返回本次触发列表"""
        alerts = db.fetch_all(
            "SELECT id, stock_code, cond, target_price, stock_name "
            "FROM price_alert WHERE user_account=%s AND status='active'",
            (user_account,))
        if not alerts:
            return []
        codes = list({a["stock_code"] for a in alerts})
        quotes = {q["stock_code"]: q for q in MarketService.get_realtime_quotes(codes)}
        triggered = []
        for a in alerts:
            q = quotes.get(a["stock_code"])
            if not q or not q.get("current_price"):
                continue
            price = float(q["current_price"])
            target = float(a["target_price"])
            hit = price >= target if a["cond"] == "gte" else price <= target
            if hit:
                db.execute(
                    "UPDATE price_alert SET status='triggered', triggered_at=NOW() WHERE id=%s",
                    (a["id"],))
                triggered.append({**a, "current_price": price})
        return triggered
