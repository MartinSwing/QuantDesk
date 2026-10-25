# -*- coding: utf-8 -*-
"""
同步股票基础数据到本地 stock_basic 表（作为行情降级缓存）
运行: python scripts/sync_basic.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import tushare as ts
from config import settings
from app.extensions import db

def main():
    ts.set_token(settings.TUSHARE_TOKEN)
    pro = ts.pro_api()
    df = pro.stock_basic(
        list_status="L",
        fields="ts_code,symbol,name,area,industry,market,list_date",
    )
    print(f"Tushare 返回 {len(df)} 条股票基础数据")

    rows = df.where(pd.notna(df), "").values.tolist()
    sql = """
        REPLACE INTO stock_basic (ts_code, symbol, name, area, industry, market, list_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    affected = db.executemany(sql, rows)
    print(f"✅ 已写入本地 stock_basic 表 {affected} 条")

if __name__ == "__main__":
    main()
