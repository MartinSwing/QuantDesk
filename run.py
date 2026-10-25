# -*- coding: utf-8 -*-
"""QuantDesk 启动入口
运行: python run.py   →  http://127.0.0.1:5001（自动打开浏览器）
"""
import threading
import webbrowser

from app import create_app

app = create_app()

if __name__ == "__main__":
    print("🚀 QuantDesk 股票交易系统启动中...")
    print("🌐 访问地址：http://127.0.0.1:5001")
    print("💡 服务运行期间请保持本窗口开启，按 Ctrl+C 停止服务")
    # 延迟 1.5 秒自动打开浏览器（等 Flask 完成监听）
    threading.Timer(1.5, webbrowser.open, args=("http://127.0.0.1:5001/login",)).start()
    app.run(debug=False, host="0.0.0.0", port=5001, threaded=True)
