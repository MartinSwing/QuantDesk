# -*- coding: utf-8 -*-
"""API 统一响应包装与参数安全解析工具"""
from flask import jsonify


def ok(data=None, msg="成功"):
    return jsonify({"code": 0, "msg": msg, "data": data})


def fail(msg, code=-1, data=None):
    return jsonify({"code": code, "msg": msg, "data": data if data is not None else {}})


def parse_int(value, name="参数", lo=None, hi=None, default=None):
    """安全整数解析：失败返回 default；越界时截断到 [lo, hi]"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v
