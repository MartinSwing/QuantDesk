# -*- coding: utf-8 -*-
"""
外部 HTTP 客户端（供 akshare / 直连行情站使用）
- 统一 trust_env=False：忽略系统代理环境变量（本机代理会干扰东财 push2/搜索接口）
- no_proxy() 上下文：临时移除 *_proxy 环境变量，包裹 akshare 调用
- 注意：东财 push2*.eastmoney.com 在部分网络下直连也会被重置，
  涉及该域名的接口（分时/资金流/五档的东财实现）一律改用新浪/腾讯替代源
"""
import os
from contextlib import contextmanager

import requests

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_patched = False


def patch_requests_no_proxy():
    """
    全局补丁：requests 默认会读取 Windows 注册表里的系统代理（trust_env），
    本机代理会破坏东财 push2/搜索接口的响应。此补丁强制非 tushare 域名的请求
    一律直连（proxies={}），tushare 保持原样（经代理可达）。
    """
    global _patched
    if _patched:
        return
    orig = requests.Session.merge_environment_settings

    def merged(self, url, proxies, stream, verify, cert):
        settings = orig(self, url, proxies, stream, verify, cert)
        if "tushare" not in (url or ""):
            settings["proxies"] = {}
        return settings

    requests.Session.merge_environment_settings = merged
    _patched = True


patch_requests_no_proxy()


@contextmanager
def no_proxy():
    """临时移除代理环境变量，退出时恢复"""
    saved = {}
    for k in list(os.environ):
        if k.lower().endswith("_proxy"):
            saved[k] = os.environ.pop(k)
    try:
        yield
    finally:
        os.environ.update(saved)


def http_get(url, params=None, headers=None, timeout=15):
    """直连 GET（不走系统代理），返回 requests.Response"""
    s = requests.Session()
    s.trust_env = False
    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    return s.get(url, params=params, headers=h, timeout=timeout)


def ak_call(fn, *args, **kwargs):
    """在无代理环境下调用 akshare 接口，异常向上抛出由调用方降级"""
    with no_proxy():
        return fn(*args, **kwargs)
