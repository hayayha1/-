import sys
import socket
import webbrowser
import threading
import time
import uvicorn

# 兼容 Windows CMD 控制台输出编码，防止 Unicode 异常
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def find_available_port(start_port=8001, max_port=8100):
    """自动检测可用端口，优先使用8001端口，若被占用自动递增至8002等"""
    for port in range(start_port, max_port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return start_port

def open_browser_later(url, delay=1.2):
    """后台延时打开浏览器，确保服务完全启动"""
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
    port = find_available_port(8001)
    web_url = f"http://127.0.0.1:{port}"
    api_docs = f"http://127.0.0.1:{port}/docs"

    print("=" * 60)
    print("  [SUCCESS] AI Customer Service System is starting...")
    print(f"  [WEB UI]  {web_url}")
    print(f"  [DOCS]    {api_docs}")
    print("=" * 60)
    print("  Server is running. Press Ctrl+C to stop.")
    print()

    # 自动唤起默认浏览器访问前端
    open_browser_later(web_url)

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
