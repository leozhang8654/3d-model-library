#!/usr/bin/env python3
"""
3D 模型库 — Flask Web App (可部署到 Render / Railway 等云平台)
功能: 静态页面 + 预转换STL服务 + 模型文件下载 + 定时自动扫描
"""

import os
import sys
import subprocess
import threading
import time
import shutil
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, abort, Response

app = Flask(__name__, static_folder='static')

# ─── 配置 ────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, 'models')
STL_CACHE_DIR = os.path.join(BASE_DIR, 'stl_cache')
SCAN_INTERVAL = 30 * 60  # 30 分钟


# ─── 自动扫描 ─────────────────────────────────────────

def run_scanner():
    """运行 scan_3d_models.py 重新生成 static/index.html"""
    scanner = os.path.join(BASE_DIR, 'scan_3d_models.py')
    if not os.path.isfile(scanner):
        print("⚠️  scan_3d_models.py 不存在，跳过扫描")
        return False
    try:
        result = subprocess.run(
            [sys.executable, scanner, MODELS_DIR],
            capture_output=True, text=True, timeout=120,
            cwd=BASE_DIR
        )
        if result.returncode == 0:
            # scanner 输出 index.html 到 BASE_DIR
            src = os.path.join(BASE_DIR, 'index.html')
            dst = os.path.join(BASE_DIR, 'static', 'index.html')
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                os.remove(src)
            lines = result.stdout.strip().split('\n')
            summary = [l for l in lines if '结果' in l]
            print(f"✅ [扫描] {''.join(summary) if summary else '已更新'}")
            return True
        else:
            print(f"❌ [扫描] 失败: {result.stderr[:300]}")
            return False
    except Exception as e:
        print(f"❌ [扫描] 异常: {e}")
        return False


def auto_rescan():
    """后台线程：每 30 分钟自动重新扫描"""
    while True:
        time.sleep(SCAN_INTERVAL)
        print(f"\n🔄 [自动扫描] 开始...")
        run_scanner()


# ─── 路由 ────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/stl/<path:filename>')
def stl_serve(filename):
    """提供预转换的 STL 文件 (构建时已转换)"""
    # 查预转换缓存
    safe_path = os.path.normpath(os.path.join(STL_CACHE_DIR, filename))
    if not safe_path.startswith(os.path.normpath(STL_CACHE_DIR)):
        abort(403)

    if os.path.isfile(safe_path):
        with open(safe_path, 'rb') as f:
            data = f.read()
        return Response(
            data,
            mimetype='application/octet-stream',
            headers={
                'Cache-Control': 'max-age=86400',
                'Access-Control-Allow-Origin': '*'
            }
        )

    # 如果缓存中没有, 尝试在 models 目录中找原始 STL
    model_path = os.path.normpath(os.path.join(MODELS_DIR, filename))
    if not model_path.startswith(os.path.normpath(MODELS_DIR)):
        abort(403)
    if os.path.isfile(model_path):
        with open(model_path, 'rb') as f:
            data = f.read()
        return Response(
            data,
            mimetype='application/octet-stream',
            headers={
                'Cache-Control': 'max-age=86400',
                'Access-Control-Allow-Origin': '*'
            }
        )

    return jsonify(ok=False, error=f"文件不存在: {filename}"), 404


@app.route('/models/<path:filename>')
def serve_model(filename):
    """直接提供模型文件下载"""
    return send_from_directory(MODELS_DIR, filename)


@app.route('/ping')
def ping():
    return jsonify(ok=True)


@app.route('/rescan', methods=['POST'])
def manual_rescan():
    """手动触发重新扫描"""
    ok = run_scanner()
    return jsonify(ok=ok)


# ─── 启动 ────────────────────────────────────────────

# 启动时先扫描一次生成最新 index.html
print("🔧 [启动] 初始扫描...")
run_scanner()

# 启动自动扫描后台线程
scan_thread = threading.Thread(target=auto_rescan, daemon=True)
scan_thread.start()
print(f"⏰ 自动扫描: 每 {SCAN_INTERVAL // 60} 分钟刷新模型列表")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7890))
    print(f"🚀 3D 模型库服务器已启动")
    print(f"📌 http://0.0.0.0:{port}")
    print(f"📁 模型目录: {MODELS_DIR}")
    print(f"📁 STL缓存: {STL_CACHE_DIR}")
    mf_count = len(list(Path(MODELS_DIR).rglob('*.3mf')))
    stl_count = len(list(Path(STL_CACHE_DIR).rglob('*.stl'))) if os.path.isdir(STL_CACHE_DIR) else 0
    print(f"📦 模型: {mf_count} 个 3MF, 预转换: {stl_count} 个")
    app.run(host='0.0.0.0', port=port, debug=False)
