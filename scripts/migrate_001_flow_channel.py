# -*- coding: utf-8 -*-
"""一次性迁移：capital_flows 增加 channel/remark 字段（幂等）"""
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
    "SELECT COLUMN_NAME FROM information_schema.columns "
    "WHERE table_schema=%s AND table_name='capital_flows'", (cfg["database"],)
)
existing = {r[0] for r in cur.fetchall()}

migrations = [
    ("channel", "ALTER TABLE capital_flows ADD COLUMN channel VARCHAR(20) NOT NULL DEFAULT '' COMMENT '资金渠道'"),
    ("remark", "ALTER TABLE capital_flows ADD COLUMN remark VARCHAR(100) NOT NULL DEFAULT '' COMMENT '备注'"),
]
for col, sql in migrations:
    if col in existing:
        print(f"跳过 {col}（已存在）")
    else:
        cur.execute(sql)
        print(f"已添加 {col}")
conn.commit()
cur.close()
conn.close()
print("迁移完成")
