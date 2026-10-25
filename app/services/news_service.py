# -*- coding: utf-8 -*-
"""
24/7 全球财经快讯服务（akshare 数据源）
- 主源：东方财富全球财经快讯 stock_info_global_em（实时快讯，7×24 更新）
- 备源：新浪全球实时财经快讯 stock_info_global_sina
- 内存缓存 2 分钟，兼顾实时性与接口友好度；任何一次成功即返回
"""
import threading
import time

from flask import current_app

_CACHE = {"items": None, "time": 0, "source": ""}
_CACHE_TTL = 120  # 缓存 2 分钟


class NewsService:
    _lock = threading.Lock()

    @staticmethod
    def get_latest_news(limit: int = 60) -> dict:
        """返回 {source, items:[{title, summary, time, url}]}，按发布时间倒序"""
        now = time.time()
        if _CACHE["items"] is not None and now - _CACHE["time"] < _CACHE_TTL:
            return {"source": _CACHE["source"], "items": _CACHE["items"][:limit]}
        with NewsService._lock:
            now = time.time()
            if _CACHE["items"] is not None and now - _CACHE["time"] < _CACHE_TTL:
                return {"source": _CACHE["source"], "items": _CACHE["items"][:limit]}
            items, source = NewsService._fetch()
            if items:
                _CACHE["items"] = items
                _CACHE["source"] = source
                _CACHE["time"] = time.time()
            return {"source": source, "items": items[:limit] if items else []}

    @staticmethod
    def _fetch():
        # 主源：东方财富全球快讯
        try:
            return NewsService._fetch_em(), "东方财富·全球快讯"
        except Exception as e:
            current_app.logger.warning(f"东方财富快讯失败: {e}")
        # 备源：新浪实时快讯
        try:
            return NewsService._fetch_sina(), "新浪财经·实时快讯"
        except Exception as e:
            current_app.logger.warning(f"新浪快讯失败: {e}")
        # 缓存兜底（过期也先用旧数据，避免开天窗）
        if _CACHE["items"]:
            return _CACHE["items"], (_CACHE["source"] or "缓存") + "（缓存）"
        return [], ""

    @staticmethod
    def _fetch_em() -> list:
        import akshare as ak
        df = ak.stock_info_global_em()
        items = []
        for r in df.itertuples(index=False):
            items.append({
                "title": str(getattr(r, "标题", "") or ""),
                "summary": str(getattr(r, "摘要", "") or ""),
                "time": str(getattr(r, "发布时间", "") or ""),
                "url": str(getattr(r, "链接", "") or ""),
            })
        items.sort(key=lambda x: x["time"], reverse=True)
        return items

    @staticmethod
    def _fetch_sina() -> list:
        import akshare as ak
        df = ak.stock_info_global_sina()
        items = []
        for r in df.itertuples(index=False):
            content = str(getattr(r, "内容", "") or "")
            # 新浪格式：内容以【标题】正文 形式组织
            title = content
            if content.startswith("【"):
                title = content[1:content.index("】")] if "】" in content else content[:30]
            items.append({
                "title": title[:40],
                "summary": content,
                "time": str(getattr(r, "时间", "") or ""),
                "url": "",
            })
        items.sort(key=lambda x: x["time"], reverse=True)
        return items
