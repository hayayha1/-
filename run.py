import sys
import socket
import webbrowser
import threading
import time
import uvicorn

# 强制 Windows CMD 控制台输出 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def find_available_port(start_port=8000, max_port=8100):
    """自动探测可用端口，默认优先使用 8000 端口"""
    for port in range(start_port, max_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start_port

def open_browser_later(url, delay=1.2):
    """后台延时自动打开浏览器"""
    def _open():
        time.sleep(delay)
        try:
            import os
            if sys.platform == "win32":
                os.system(f'start "" "{url}"')
            else:
                webbrowser.open(url)
        except Exception:
            try:
                webbrowser.open(url)
            except Exception:
                pass
    threading.Thread(target=_open, daemon=True).start()

if __name__ == "__main__":
    port = find_available_port(8000)
    web_url = f"http://localhost:{port}"
    api_docs = f"http://localhost:{port}/docs"

    print("=" * 68)
    print("  🤖 智语云 · AI 智能客服 SaaS 平台正在启动...")
    print(f"  🌐 产品官网 & 订阅中心: {web_url}/")
    print(f"  📖 Swagger API 接口文档: {api_docs}")
    print("=" * 68)
    print("  演示快捷账号体系（除平台超管外均归属同一租户与产品线闭环）：")
    print("   👑 平台超级管理员: platform_admin / admin123")
    print("   🏢 租户超级管理员: admin_tenant   / admin123")
    print("   📚 知识库专家主管: km_001         / admin123")
    print("   🎧 一线人工客服:   agent_1028     / admin123")
    print("   🙋 终端普通客户:   user_001       / 123456")
    print("=" * 68)
    print("  💡 服务运行中，正在为您自动打开默认浏览器。按 Ctrl+C 可停止运行。")
    print()

    # 自动在默认浏览器打开产品官网
    open_browser_later(web_url)

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
