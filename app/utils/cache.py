# -*- coding: utf-8 -*-
"""
进程内 TTL 缓存（双重检查锁，线程安全）
- cached(key, ttl, fn)：fn() 返回真值则缓存 ttl 秒
- neg_ttl：失败负缓存——fn() 返回空值时记 neg_ttl 秒防连打（0 = 不记）
替换各服务此前复制的手写 _cached/_CACHE/_lock 变体
"""
import threading
import time

_store = {}
_lock = threading.Lock()


def cached(key, ttl, fn, neg_ttl=0):
    now = time.time()
    hit = _store.get(key)
    if hit:
        fresh_ok = hit["data"] is not None and now - hit["time"] < ttl
        neg_ok = hit["data"] is None and neg_ttl > 0 and now - hit["time"] < neg_ttl
        if fresh_ok or neg_ok:
            return hit["data"]
    with _lock:
        now = time.time()
        hit = _store.get(key)
        if hit:
            fresh_ok = hit["data"] is not None and now - hit["time"] < ttl
            neg_ok = hit["data"] is None and neg_ttl > 0 and now - hit["time"] < neg_ttl
            if fresh_ok or neg_ok:
                return hit["data"]
        data = fn()
        # 成功缓存 ttl；失败（空值）仅在 neg_ttl>0 时记负缓存
        _store[key] = {"data": data if data else None, "time": time.time()}
        return data


def invalidate(key=None):
    """清除指定 key；不传则全清"""
    with _lock:
        if key is None:
            _store.clear()
        else:
            _store.pop(key, None)
