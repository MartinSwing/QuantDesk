# -*- coding: utf-8 -*-
"""
QuantDesk 数据库初始化脚本
- 创建 quantdesk 数据库及全部数据表
- 创建默认管理员账号 000000 / admin123
运行: python scripts/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql
from werkzeug.security import generate_password_hash
from config import settings

DATABASE = settings.MYSQL_CONFIG["database"]

TABLES = {
    # 系统用户（登录凭证）
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            user_account  VARCHAR(20)  NOT NULL,
            password_hash VARCHAR(255) NOT NULL COMMENT 'werkzeug加密密码',
            role          ENUM('user','admin') NOT NULL DEFAULT 'user',
            status        ENUM('active','disabled') NOT NULL DEFAULT 'active',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_account (user_account)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统用户表'
    """,
    # 用户资料（与登录凭证分离，便于扩展第三方登录）
    "user_profiles": """
        CREATE TABLE IF NOT EXISTS user_profiles (
            id           INT AUTO_INCREMENT PRIMARY KEY,
            user_account VARCHAR(20)  NOT NULL,
            real_name    VARCHAR(50)  NOT NULL,
            id_card      CHAR(18)     NOT NULL,
            birthday     DATE         NOT NULL,
            phone        CHAR(11)     NOT NULL,
            bank_card    VARCHAR(32)  NOT NULL,
            province     VARCHAR(50)  NOT NULL,
            occupation   VARCHAR(100) NOT NULL,
            address      VARCHAR(255) NOT NULL DEFAULT '',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_account (user_account),
            UNIQUE KEY uk_id_card (id_card),
            UNIQUE KEY uk_phone (phone)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户资料表'
    """,
    # 资金账户（新增冻结余额字段，为限价单/预冻结扩展预留）
    "accounts": """
        CREATE TABLE IF NOT EXISTS accounts (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            user_account      VARCHAR(20) NOT NULL,
            available_balance DECIMAL(14,2) NOT NULL DEFAULT 0.00 COMMENT '可用资金',
            frozen_balance    DECIMAL(14,2) NOT NULL DEFAULT 0.00 COMMENT '冻结资金',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_account (user_account)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资金账户表'
    """,
    # 持仓表（新增可用数量，支持T+1；UNIQUE防止重复持仓行）
    "positions": """
        CREATE TABLE IF NOT EXISTS positions (
            id                 INT AUTO_INCREMENT PRIMARY KEY,
            user_account       VARCHAR(20) NOT NULL,
            stock_code         VARCHAR(12) NOT NULL,
            stock_name         VARCHAR(50) NOT NULL DEFAULT '',
            quantity           INT NOT NULL DEFAULT 0 COMMENT '总持仓',
            available_quantity INT NOT NULL DEFAULT 0 COMMENT '可卖数量(T+1冻结用)',
            avg_cost           DECIMAL(10,3) NOT NULL DEFAULT 0.000 COMMENT '摊薄成本价',
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_stock (user_account, stock_code),
            KEY idx_user (user_account)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='持仓表'
    """,
    # 委托/成交记录（新增手续费与印花税、股票名称冗余）
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            id           BIGINT AUTO_INCREMENT PRIMARY KEY,
            order_no     VARCHAR(32)  NOT NULL COMMENT '订单号',
            user_account VARCHAR(20)  NOT NULL,
            stock_code   VARCHAR(12)  NOT NULL,
            stock_name   VARCHAR(50)  NOT NULL DEFAULT '',
            side         ENUM('buy','sell') NOT NULL,
            price        DECIMAL(10,3) NOT NULL COMMENT '成交价',
            quantity     INT NOT NULL COMMENT '成交数量(股)',
            amount       DECIMAL(14,2) NOT NULL COMMENT '成交金额',
            fee          DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '佣金',
            tax          DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '印花税(卖出)',
            status       ENUM('filled','failed') NOT NULL DEFAULT 'filled',
            remark       VARCHAR(255) NOT NULL DEFAULT '',
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_order_no (order_no),
            KEY idx_user_time (user_account, created_at),
            KEY idx_stock (stock_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易委托/成交记录'
    """,
    # 资金流水（充值/提现统一，命名比旧库 user_recharge 更通用）
    "capital_flows": """
        CREATE TABLE IF NOT EXISTS capital_flows (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY,
            flow_no     VARCHAR(32) NOT NULL COMMENT '流水号',
            user_account VARCHAR(20) NOT NULL,
            flow_type   ENUM('deposit','withdraw') NOT NULL COMMENT '入金/出金',
            amount      DECIMAL(14,2) NOT NULL,
            status      ENUM('success','failed') NOT NULL DEFAULT 'success',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_flow_no (flow_no),
            KEY idx_user_time (user_account, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资金流水表'
    """,
    # 自选股
    "watchlist": """
        CREATE TABLE IF NOT EXISTS watchlist (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_account VARCHAR(20) NOT NULL,
            stock_code  VARCHAR(12) NOT NULL,
            stock_name  VARCHAR(50) NOT NULL DEFAULT '',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_user_stock (user_account, stock_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自选股表'
    """,
    # 股票基础信息（旧库为text类型，改为严格varchar+主键）
    "stock_basic": """
        CREATE TABLE IF NOT EXISTS stock_basic (
            ts_code   VARCHAR(12) PRIMARY KEY COMMENT 'tushare代码 如000001.SZ',
            symbol    VARCHAR(10) NOT NULL COMMENT '6位代码',
            name      VARCHAR(50) NOT NULL,
            area      VARCHAR(20) NOT NULL DEFAULT '',
            industry  VARCHAR(30) NOT NULL DEFAULT '',
            market    VARCHAR(20) NOT NULL DEFAULT '',
            list_date VARCHAR(8)  NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票基础信息缓存'
    """,
    # 股票日线（新增trade_date单列索引：旧库缺此索引导致查最新交易日需全表扫12秒）
    "stock_daily": """
        CREATE TABLE IF NOT EXISTS stock_daily (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY,
            ts_code    VARCHAR(12) NOT NULL,
            trade_date DATE NOT NULL,
            open       DECIMAL(10,3),
            high       DECIMAL(10,3),
            low        DECIMAL(10,3),
            close      DECIMAL(10,3),
            pre_close  DECIMAL(10,3),
            pct_chg    DECIMAL(8,4),
            vol        DECIMAL(16,2) COMMENT '成交量(手)',
            amount     DECIMAL(16,2) COMMENT '成交额(千元)',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_code_date (ts_code, trade_date),
            KEY idx_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票日线缓存'
    """,
    # 指数日线（新增：旧项目指数K线完全依赖tushare无本地缓存）
    "index_daily": """
        CREATE TABLE IF NOT EXISTS index_daily (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY,
            ts_code    VARCHAR(12) NOT NULL,
            trade_date DATE NOT NULL,
            open       DECIMAL(12,2),
            high       DECIMAL(12,2),
            low        DECIMAL(12,2),
            close      DECIMAL(12,2),
            pre_close  DECIMAL(12,2),
            vol        DECIMAL(18,2),
            amount     DECIMAL(18,2),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_code_date (ts_code, trade_date),
            KEY idx_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指数日线缓存'
    """,
}


def main():
    # 1. 连接服务器（不指定库），创建数据库
    cfg = {k: v for k, v in settings.MYSQL_CONFIG.items() if k != "database"}
    conn = pymysql.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DATABASE}` "
                "DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        print(f"✅ 数据库 {DATABASE} 就绪")
    finally:
        conn.close()

    # 2. 建表
    conn = pymysql.connect(**settings.MYSQL_CONFIG)
    try:
        with conn.cursor() as cur:
            for name, ddl in TABLES.items():
                cur.execute(ddl)
                print(f"✅ 表 {name} 就绪")
        conn.commit()
    finally:
        conn.close()

    # 3. 默认管理员
    conn = pymysql.connect(**settings.MYSQL_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO users (user_account, password_hash, role) VALUES (%s,%s,%s)",
                ("000000", generate_password_hash("admin123"), "admin"),
            )
            cur.execute(
                "INSERT IGNORE INTO accounts (user_account) VALUES (%s)",
                ("000000",),
            )
        conn.commit()
        print("✅ 默认管理员 000000 / admin123")
    finally:
        conn.close()

    print("\n🎉 数据库初始化完成！下一步运行: python scripts/sync_basic.py 同步股票基础数据")


if __name__ == "__main__":
    main()
