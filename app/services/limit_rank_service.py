# -*- coding: utf-8 -*-
"""
涨跌榜服务：Tushare Pro webclient（新浪 Market_Center 网页采集源，0 积分）
数据源：新浪 vip.stock.finance.sina.com.cn Market_Center.getHQNodeData（按涨跌幅排序）
性能要点：**不做全市场分页**（5334 只×70 页串行需 14~30s+）。
利用接口的 sort=changepercent 排序能力，只拉取排序头部（涨停方向降序 / 跌停方向升序），
逐页自适应：页尾涨跌幅已越过 ±4.9% 阈值（覆盖 ST 5% 档）即停止，通常 2~3 页，两个方向并行请求，
总耗时 ~2s。
范围：沪主板(sh60) / 深主板(sz00) / 创业板(sz30) / 科创板(sh68)，务必排除北交所(bj*)
涨跌停判定：昨收 × 候选档位（主板10%、创业板/科创板20%、ST 5%/10% 两种情形）四舍五入到分，
           现价触及该价才算封板（比单纯涨跌幅阈值更精确）
- 成功缓存 60 秒，失败负缓存 30 秒
"""
import logging

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

from app.datasources.web_client import http_get
from app.utils.cache import cached

_TTL = 60
_NEG_TTL = 30

_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent"
        "&asc={asc}&node=hs_a&symbol=&_s_r_a=page")
_MAX_PAGES = 6
_THRESHOLD = 4.9   # 覆盖 ST 5% 档的最小排序边界

_MARKETS = {"sh60": "沪主板", "sz00": "深主板", "sz30": "创业板", "sh68": "科创板"}


def _f(v, nd=2):
    try:
        if v is None or v != v:
            return None
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


def _market(code: str, name: str):
    """symbol 形如 sh600000 / sz000001 / bj920000 → 市场标签；北交所返回 None"""
    if code.startswith("bj"):
        return None
    for pre, label in _MARKETS.items():
        if code.startswith(pre):
            return "ST股" if "ST" in name.upper() else label
    return None


def _limit_ratios(code: str, name: str) -> tuple:
    """候选涨跌幅限制档位：创业板/科创板固定 20%；
    其他板块 10%，名称带 ST 并入 5%（ST 股实际限制存在 5%/10% 两种情形，
    如摘帽数据滞后或特殊状态，故用候选集精确匹配而非单一假设）"""
    if code.startswith(("sz30", "sh68")):
        return (0.20,)
    if "ST" in name.upper():
        return (0.05, 0.10)
    return (0.10,)


def _to_ts_code(code: str) -> str:
    """sh600000 → 600000.SH（K线页路由格式）"""
    return f"{code[2:]}.{code[:2].upper()}"


def _get_json(url):
    """新浪该接口返回标准 JSON，但部分场景 key 无引号 → json 失败时正则补引号"""
    r = http_get(url)
    text = r.text if hasattr(r, "text") else str(r)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return json.loads(re.sub(r"([{,])(\w+):", r'\1"\2":', text))


def _fetch_sorted(asc: int) -> list:
    """按涨跌幅排序逐页拉取：页尾越过阈值（±4.9%）即停，最多 _MAX_PAGES 页"""
    rows = []
    for page in range(1, _MAX_PAGES + 1):
        try:
            data = _get_json(_URL.format(page=page, asc=asc))
        except Exception as e:
            logger.warning(f"⚠️ 涨跌榜分页失败(p{page}): {str(e)[:80]}", flush=True)
            break
        if not data:
            break
        rows.extend(data)
        tail = _f(data[-1].get("changepercent"), 3) or 0.0
        if (asc == 0 and tail < _THRESHOLD) or (asc == 1 and tail > -_THRESHOLD):
            break
    return rows


class LimitRankService:

    @staticmethod
    def get_limit_rank() -> dict:
        def _load():
            try:
                return LimitRankService._fetch()
            except Exception as e:
                logger.warning(f"⚠️ 涨跌榜不可用: {str(e)[:100]}", flush=True)
                return None
        # 成功缓存 60s；失败负缓存 30s（返回 {} 使前端展示"暂不可用"而非报错）
        return cached(("limit_rank",), _TTL, _load, neg_ttl=_NEG_TTL) or {}

    @staticmethod
    def _fetch() -> dict:
        # 两个排序方向并行请求，各自串行分页
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_up, f_down = pool.submit(_fetch_sorted, 0), pool.submit(_fetch_sorted, 1)
            raw_up, raw_down = f_up.result(), f_down.result()

        up, down = [], []
        tick = ""
        for r in raw_up + raw_down:
            sym = str(r.get("symbol") or "")
            name = str(r.get("name") or "")
            mkt = _market(sym, name)
            if not mkt:            # 北交所及其他非四市场：排除
                continue
            price = _f(r.get("trade"))
            pre = _f(r.get("settlement"))
            if price is None or pre is None or pre <= 0 or price <= 0:
                continue
            tick = str(r.get("ticktime") or tick)
            row = {
                "code": _to_ts_code(sym),
                "name": name,
                "price": price,
                "pct": _f(r.get("changepercent"), 3),
                "chg": _f(r.get("pricechange")),
                "amount_yi": _f((r.get("amount") or 0) / 1e8),
                "turnover": _f(r.get("turnoverratio")),
                "market": mkt,
            }
            # 涨停/跌停判定：现价触及任一候选档位的涨跌停价（容差 0.005 应对舍入）
            hit_up = hit_down = False
            for ratio in _limit_ratios(sym, name):
                if abs(price - round(pre * (1 + ratio), 2)) < 0.005:
                    hit_up = True
                if abs(price - round(pre * (1 - ratio), 2)) < 0.005:
                    hit_down = True
            if hit_up:
                up.append(row)
            elif hit_down:
                down.append(row)

        # 涨停/跌停榜均按成交额降序
        up.sort(key=lambda x: (x["amount_yi"] or 0), reverse=True)
        down.sort(key=lambda x: (x["amount_yi"] or 0), reverse=True)
        if not up and not down:
            return {}
        return {
            "tick": tick,
            "up": up,
            "down": down,
            "source": "Tushare webclient(新浪行情快照)",
        }
