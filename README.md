# QuantDesk 量化股票交易系统

模块化、可扩展的 A 股模拟交易系统（毕业设计级全栈项目）。
数据源：Tushare Pro；数据库：MySQL；后端：Flask（工厂模式 + Blueprint 分域 + 三层架构）。

## 快速开始

```bash
# 1. 初始化数据库（建库建表 + 默认管理员 000000/admin123）
python scripts/init_db.py

# 2. 同步股票基础数据（5564只，作为行情降级缓存）
python scripts/sync_basic.py

# 3. 启动（端口 5001，避免与旧系统 5000 冲突）
python run.py
# → http://127.0.0.1:5001
```

依赖见 `requirements.txt`；Python 3.11+ 可运行。

## 配置管理

所有敏感信息集中在 `config/.env`（勿提交 Git）：

| 配置项 | 说明 |
|---|---|
| TUSHARE_TOKEN | Tushare Pro token |
| MYSQL_HOST / PORT / USER / PASSWORD / DATABASE | 数据库连接 |
| FLASK_SECRET_KEY | Flask session 密钥 |

`config/settings.py` 负责加载 `.env` 并集中暴露常量（含交易规则：佣金万2.5最低5元、印花税万5仅卖出、一手100股）。

## 架构与模块划分

```
config/          配置中心（.env 敏感信息 + settings.py 常量）
app/
├── __init__.py  应用工厂 create_app()
├── extensions.py  连接池 + Flask-Login（通用组件）
├── datasources/   数据获取层
│   ├── tushare_client.py  Tushare封装（单例、失败冷却、交易日历/17点规则）
│   └── local_cache.py     MySQL行情缓存读写（降级数据源）
├── models/        数据访问层（DAO）
│   ├── user_dao.py        用户/资料
│   ├── asset_dao.py       资金账户
│   ├── trade_dao.py       持仓/订单/资金流水
│   ├── favorite_dao.py    自选股
│   └── market_dao.py      （行情缓存查询在 datasources 中）
├── services/      业务逻辑层
│   ├── trading_service.py   买卖（资金校验、佣金/印花税、事务一致性、T+1开关）
│   ├── asset_service.py     资产汇总/出入金
│   ├── market_service.py    行情组合（在线优先→写缓存→失败降级本地）
│   ├── backtest_service.py  策略回测引擎（7 策略 + 对比 + 参数寻优）
│   ├── factor_service.py    多因子选股打分
│   ├── regime_service.py    市场状态识别
│   ├── moneyflow_service.py 资金流向（主力/北向）
│   ├── valuation_service.py 价值投资指标（PE/PB/股息率/市值）
│   ├── earnings_calendar_service.py 财报日历
│   ├── hk_service.py        港股行情（akshare）
│   └── ...（其余服务）
├── api/           REST 接口层（Blueprint 按域拆分，新增功能=新增bp）
│   ├── auth_bp.py     /login /register /forget_pwd /logout
│   ├── market_bp.py   /api/market/*   指数、实时行情、搜索、K线
│   ├── trade_bp.py    /api/trade/*    买卖、成交记录
│   ├── asset_bp.py    /api/asset/*    资产、出入金、流水
│   ├── favorite_bp.py /api/favorite/* 自选股
│   └── admin_bp.py    /api/admin/*    用户管理（admin_required 装饰器）
├── views/page_bp.py  页面路由
├── templates/     Jinja2 模板（base.html 继承体系）
└── static/        样式
scripts/          init_db.py 建库 / sync_basic.py 数据同步
```

**扩展新功能的方式**：新增一个 DAO + Service + Blueprint，在 `api/__init__.py` 注册即可；
新数据表在 `scripts/init_db.py` 的 TABLES 字典里追加。

## 数据库设计（quantdesk，11张表）

相对旧版（Stock_Market/stock_data）的改进：

| 改进点 | 旧版问题 → 新版方案 |
|---|---|
| 类型严格化 | stock_basic 全 text 列 → varchar + 主键 |
| 交易成本 | 无手续费 → orders 记录佣金+印花税 |
| 资金冻结 | 仅 available_balance → 增加 frozen_balance（为限价单预留） |
| T+1 支持 | 无 → positions 拆分 quantity / available_quantity（配置开关） |
| 事务安全 | 裸 UPDATE → SELECT ... FOR UPDATE + 显式事务 |
| 查询性能 | stock_daily 缺 trade_date 索引（全表扫12秒）→ 增加 idx_trade_date |
| 指数缓存 | 无 → index_daily 表 |
| 资金流水 | user_recharge → capital_flows（入金/出金统一） |

核心表：`users`（登录）、`user_profiles`（实名资料）、`accounts`（资金）、
`positions`（持仓）、`orders`（成交）、`capital_flows`（流水）、`watchlist`（自选）、
`stock_basic` / `stock_daily` / `index_daily`（行情缓存）。

## 可靠性设计（数据源降级）

Tushare 线路不稳时（token间歇失效/限流/网络异常），系统自动降级：

1. **失败冷却**：连续失败 3 次 → 5 分钟冷却期，期间直接读本地缓存，页面不卡死；
2. **读写缓存**：在线行情成功后自动写入 `stock_daily` / `index_daily`，作为降级数据；
3. **来源标注**：接口返回 `source: tushare / local_cache`，前端展示"实时/缓存"。

## 已实现功能

**用户与安全**
- 注册（自动分配六位账号）、登录（失败锁定限流）、身份验证找回密码（短时会话令牌）、管理员用户管理
- 安全加固：密码哈希存储、SQL 全参数化、`admin_required` 鉴权、Jinja 自动转义、密钥集中 `.env` 管理

**行情与数据**
- 六大指数概览与 K 线、个股 K 线（日 K + 分时 + 五档）、技术指标四件套（MACD/KDJ/RSI/BOLL/MA）、时间区间切换（近半年/近3年/近5年/自定义）
- 行业热力图 + 行业详情、涨停板复盘（涨跌停双榜 + 龙虎榜）、24-7 快讯
- **港股市场**（实时行情 + K 线，akshare 数据源）

**选股与量化**
- 条件选股（9 维筛选 + 服务端分页排序）
- **多因子选股打分**（估值 PE/PB + 价值股息率 + 动量涨跌幅，横截面百分位加权排名）
- **策略回测**（双均线/MACD/KDJ/布林带/唐奇安/动量/网格 7 策略 + 多策略对比 + 参数寻优热力图）
- **市场状态识别**（均线排列 + 波动率分位三分类，输出适配策略建议）

**价值投资与资金面**
- **价值投资指标**（PE/PB/股息率/总市值历史趋势 + 历史分位 + 日期标注）
- **资金流向**（个股主力/超大单/大单/中单/小单净流入 + 北向资金）
- **财报日历**（业绩快报营收/利润同比 + 财报披露日历）

**模拟交易**
- 入金/出金、按行情价买卖（佣金+印花税）、持仓摊薄成本、收益曲线（账户 vs 沪深300）、自选股、价格预警

## 默认账号（仅本地模拟盘测试用）

- 管理员：`000000 / admin123`
- 测试用户：`000001 / test123`（含模拟交易数据）

> 以上均为本地开发测试账号，非生产凭据；正式部署请自行修改或禁用。
