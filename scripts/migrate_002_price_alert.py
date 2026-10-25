# -*- coding: utf-8 -*-
"""一次性迁移：新建 price_alert 价格提醒表（幂等）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql

from config import settings

cfg = settings.MYSQL_CONFIG
conn = pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                       password=cfg["password"], database=cfg["database"],
                       charset=cfg["charset"])
cur = conn.cursor()
cur.execute(
    "SELECT TABLE_NAME FROM information_schema.tables "
    "WHERE table_schema=%s AND table_name='price_alert'", (cfg["database"],)
)
if cur.fetchone():
    print("跳过 price_alert（表已存在）")
else:
    cur.execute("""
        CREATE TABLE price_alert (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_account VARCHAR(20) NOT NULL,
            stock_code VARCHAR(12) NOT NULL,
            stock_name VARCHAR(20) NOT NULL DEFAULT '',
            cond ENUM('gte','lte') NOT NULL COMMENT 'gte=涨到 lte=跌到',
            target_price DECIMAL(12,3) NOT NULL,
            status ENUM('active','triggered','cancelled') NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            triggered_at DATETIME NULL,
            INDEX idx_user_status (user_account, status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='价格提醒'
    """)
    print("已创建 price_alert 表")
conn.commit()
cur.close()
conn.close()
print("迁移完成")
