# -*- coding: utf-8 -*-
"""
QuantDesk 全局配置中心
敏感信息（token、数据库密码等）统一存放在 config/.env，本文件负责加载并暴露常量。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = Path(__file__).resolve().parent / ".env"


def _load_env(path: Path) -> dict:
    """极简 .env 解析器（避免额外依赖 python-dotenv）"""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


_ENV = _load_env(ENV_FILE)


def env(key: str, default: str = "") -> str:
    # 系统环境变量优先，其次 .env
    return os.environ.get(key) or _ENV.get(key) or default


# ---------- Tushare ----------
TUSHARE_TOKEN = env("TUSHARE_TOKEN")

# ---------- MySQL ----------
MYSQL_CONFIG = {
    "host": env("MYSQL_HOST", "localhost"),
    "port": int(env("MYSQL_PORT", "3307")),
    "user": env("MYSQL_USER", "admin"),
    "password": env("MYSQL_PASSWORD"),   # 无默认：禁止弱口令兜底
    "database": env("MYSQL_DATABASE", "quantdesk"),
    "charset": "utf8mb4",
}

# ---------- Flask ----------
SECRET_KEY = env("FLASK_SECRET_KEY")     # 无默认：禁止可预测兜底

# 启动强校验：关键凭据缺失直接拒绝启动，避免静默回退弱配置
if not SECRET_KEY:
    raise RuntimeError(
        "缺少安全配置 FLASK_SECRET_KEY，请在 config/.env 中配置强随机值"
        "（生成：python -c \"import secrets; print(secrets.token_hex(32))\"）")
if not MYSQL_CONFIG["password"]:
    raise RuntimeError("缺少安全配置 MYSQL_PASSWORD，请在 config/.env 中配置")

# ---------- 交易规则（模拟盘） ----------
TRADE_RULES = {
    "commission_rate": 0.00025,   # 佣金费率 万2.5
    "commission_min": 5.0,        # 最低佣金 5 元
    "stamp_tax_rate": 0.0005,     # 印花税（仅卖出收取）万5
    "min_trade_num": 100,         # 一手 = 100 股，交易数量须为100的整数倍
    "t_plus_1": False,            # 模拟盘简化为 T+0；置 True 可启用当日买入冻结
}

# ---------- 行情数据源 ----------
MARKET_RULES = {
    "basic_cache_seconds": 3600,      # 股票基础信息内存缓存时长
    "tushare_fail_threshold": 3,      # 连续失败N次进入冷却
    "tushare_cooldown_seconds": 300,  # 冷却时长，期间直接用本地缓存
    "target_date_after": "17:00",     # 交易日17点后视为当日数据
}
