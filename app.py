import os
import sys
import threading
import time
import requests
import uvicorn
import webview

# Add the current directory to sys.path so it can find api and review_assistant
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.main import app

def run_server():
    # Run Uvicorn without hot-reloading for production
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

def verify_server():
    for _ in range(20):
        try:
            r = requests.get("http://127.0.0.1:8000/api/health", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

if __name__ == '__main__':
    # Start FastAPI server in a daemon thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for server to start
    if not verify_server():
        print("Failed to start internal server", file=sys.stderr)
        sys.exit(1)

    # Create webview window
    window = webview.create_window(
        'Review Assistant', 
        'http://127.0.0.1:8000',
        width=1200,
        height=800,
        min_size=(800, 600)
    )
    
    # Start the native window event loop
    webview.start()
