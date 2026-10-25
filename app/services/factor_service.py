# -*- coding: utf-8 -*-
"""
多因子选股打分：估值(PE/PB)、价值(股息率)、动量(涨跌幅) 横截面百分位加权排名
- 复用 ScreenerService 的全市场快照（daily+daily_basic 拼装，无逐股拉取，规避限频）
- 每个因子对全市场（或指定行业）做百分位打分 0~100；低估值因子反转（越低分越高）
- 加权求和 → 综合得分降序排名
"""
import json

from app.services.screener_service import ScreenerService
from app.utils.cache import cached

_TTL = 600

# 因子 key -> (中文名, 是否越低越好)
FACTORS = {
    "pe": {"label": "估值·PE", "low_better": True},
    "pb": {"label": "估值·PB", "low_better": True},
    "dv": {"label": "价值·股息率", "low_better": False},
    "pct": {"label": "动量·涨跌幅", "low_better": False},
}


def _pct_rank(vals):
    """横截面百分位打分 {index: 0~100}，缺失值不参与"""
    clean = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(clean) < 2:
        return {i: 50.0 for i, _ in clean}
    clean.sort(key=lambda x: x[1])
    n = len(clean) - 1
    return {i: round(pos / n * 100, 1) for pos, (i, _) in enumerate(clean)}


class FactorService:

    @staticmethod
    def rank(weights: dict = None, industry: str = "", top_n: int = 50) -> dict:
        """weights: {pe, pb, dv, pct} 权重；industry 可选行业过滤"""
        def _calc():
            data = ScreenerService.get_universe()
            if not data:
                return {}
            rows = [r for r in data["rows"]
                    if (not industry or r["industry"] == industry)]
            if len(rows) < 2:
                return {"rows": [], "total": 0}

            w = {k: float((weights or {}).get(k, 25)) for k in FACTORS}
            tot = sum(w.values()) or 1
            w = {k: v / tot for k, v in w.items()}          # 归一化

            scores = {k: _pct_rank([r.get(k) for r in rows]) for k in FACTORS}
            for k, info in FACTORS.items():                 # 低估值因子反转
                if info["low_better"]:
                    scores[k] = {i: round(100 - s, 1) for i, s in scores[k].items()}

            out = []
            for i, r in enumerate(rows):
                f = {k: scores[k].get(i, 0.0) for k in FACTORS}   # 缺失给 0 分
                score = sum(w[k] * f[k] for k in FACTORS)
                out.append({**r, "score": round(score, 2),
                            "f_pe": f["pe"], "f_pb": f["pb"],
                            "f_dv": f["dv"], "f_pct": f["pct"]})
            out.sort(key=lambda x: x["score"], reverse=True)
            return {
                "trade_date": data.get("trade_date", ""),
                "total": len(out),
                "weights": {k: round(w[k], 4) for k in FACTORS},
                "rows": out[:top_n],
            }

        key = ("factors", industry, json.dumps(sorted((weights or {}).items())), top_n)
        return cached(key, _TTL, _calc)
