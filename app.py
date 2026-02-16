#!/usr/bin/env python3
"""
3D 模型库 — Flask Web App (可部署到 Render / Railway 等云平台)
功能: 静态页面 + 3MF→STL 实时转换 + 模型文件下载
"""

import os
import struct
import math
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from flask import Flask, send_from_directory, jsonify, request, abort

app = Flask(__name__, static_folder='static')

# ─── 配置 ────────────────────────────────────────────
MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
ALLOWED_EXT = {'.3mf', '.stl', '.obj', '.step', '.stp', '.gcode',
               '.blend', '.fbx', '.ply', '.amf', '.scad', '.f3d'}

# STL 缓存
_stl_cache = {}

NS = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02',
      'p': 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'}


# ─── 3MF → STL 转换 ─────────────────────────────────
def _parse_model_xml(xml_bytes):
    root = ET.fromstring(xml_bytes)
    meshes = {}
    for obj in root.findall('.//m:object', NS):
        oid = obj.get('id')
        mesh_el = obj.find('m:mesh', NS)
        if mesh_el is None:
            continue
        vertices_el = mesh_el.find('m:vertices', NS)
        triangles_el = mesh_el.find('m:triangles', NS)
        if vertices_el is None or triangles_el is None:
            continue
        verts = []
        for v in vertices_el:
            verts.append(float(v.get('x', 0)))
            verts.append(float(v.get('y', 0)))
            verts.append(float(v.get('z', 0)))
        tris = []
        for t in triangles_el:
            tris.append(int(t.get('v1', 0)))
            tris.append(int(t.get('v2', 0)))
            tris.append(int(t.get('v3', 0)))
        if verts and tris:
            meshes[oid] = (verts, tris)
    components_map = {}
    for obj in root.findall('.//m:object', NS):
        oid = obj.get('id')
        comps_el = obj.find('m:components', NS)
        if comps_el is not None:
            comp_list = []
            for c in comps_el.findall('m:component', NS):
                comp_list.append((
                    c.get('objectid'),
                    c.get(f'{{{NS["p"]}}}path', '')
                ))
            if comp_list:
                components_map[oid] = comp_list
    build_items = []
    for item in root.findall('.//m:build/m:item', NS):
        build_items.append(item.get('objectid'))
    return meshes, components_map, build_items


def convert_3mf_to_stl(filepath):
    mtime = os.path.getmtime(filepath)
    cache_key = f"{filepath}:{mtime}"
    if cache_key in _stl_cache:
        return _stl_cache[cache_key]

    all_verts = []
    all_tris = []
    vert_offset = 0

    with zipfile.ZipFile(filepath) as z:
        model_data = {}
        for name in z.namelist():
            if not name.endswith('.model'):
                continue
            key = '/' + name if not name.startswith('/') else name
            xml_data = z.read(name)
            model_data[key] = _parse_model_xml(xml_data)

        def add_mesh(verts, tris):
            nonlocal vert_offset
            all_verts.extend(verts)
            for i in range(0, len(tris), 3):
                all_tris.append(tris[i] + vert_offset)
                all_tris.append(tris[i+1] + vert_offset)
                all_tris.append(tris[i+2] + vert_offset)
            vert_offset += len(verts) // 3

        def collect(oid, model_path):
            if model_path not in model_data:
                return
            meshes, comps_map, _ = model_data[model_path]
            if oid in meshes:
                add_mesh(*meshes[oid])
            if oid in comps_map:
                for comp_oid, comp_path in comps_map[oid]:
                    ref = comp_path if comp_path else model_path
                    if not ref.startswith('/'):
                        ref = '/' + ref
                    collect(comp_oid, ref)

        main_key = '/3D/3dmodel.model'
        if main_key in model_data:
            _, _, build_items = model_data[main_key]
            if build_items:
                for item_oid in build_items:
                    collect(item_oid, main_key)
            else:
                for meshes, _, _ in model_data.values():
                    for oid, (v, t) in meshes.items():
                        add_mesh(v, t)
        else:
            for meshes, _, _ in model_data.values():
                for oid, (v, t) in meshes.items():
                    add_mesh(v, t)

    num_tris = len(all_tris) // 3
    if num_tris == 0:
        return None

    stl_size = 80 + 4 + num_tris * 50
    stl_data = bytearray(stl_size)
    struct.pack_into('<I', stl_data, 80, num_tris)

    pos = 84
    v = all_verts
    t = all_tris
    pack_into = struct.pack_into
    sqrt = math.sqrt
    for i in range(num_tris):
        i3 = i * 3
        a = t[i3] * 3;     ax, ay, az = v[a], v[a+1], v[a+2]
        b = t[i3+1] * 3;   bx, by, bz = v[b], v[b+1], v[b+2]
        c = t[i3+2] * 3;   cx, cy, cz = v[c], v[c+1], v[c+2]
        ux, uy, uz = bx-ax, by-ay, bz-az
        vx, vy, vz = cx-ax, cy-ay, cz-az
        nx = uy*vz - uz*vy
        ny = uz*vx - ux*vz
        nz = ux*vy - uy*vx
        ln = sqrt(nx*nx + ny*ny + nz*nz)
        if ln > 0:
            nx /= ln; ny /= ln; nz /= ln
        pack_into('<12fH', stl_data, pos, nx,ny,nz, ax,ay,az, bx,by,bz, cx,cy,cz, 0)
        pos += 50

    result = bytes(stl_data)
    _stl_cache[cache_key] = result
    print(f"  🔄 转换: {os.path.basename(filepath)} → STL ({vert_offset} verts, {num_tris} tris)")
    return result


# ─── 路由 ────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/stl/<path:filename>')
def stl_convert(filename):
    """3MF → STL 实时转换"""
    safe_path = os.path.normpath(os.path.join(MODELS_DIR, filename))
    if not safe_path.startswith(os.path.normpath(MODELS_DIR)):
        abort(403)
    if not os.path.isfile(safe_path):
        # 也搜索子目录
        for root, dirs, files in os.walk(MODELS_DIR):
            for f in files:
                if f == os.path.basename(filename):
                    safe_path = os.path.join(root, f)
                    break
        if not os.path.isfile(safe_path):
            return jsonify(ok=False, error=f"文件不存在: {filename}"), 404
    try:
        stl_data = convert_3mf_to_stl(safe_path)
        if stl_data is None:
            return jsonify(ok=False, error="3MF 文件中未找到网格数据"), 500
        from flask import Response
        return Response(
            stl_data,
            mimetype='application/octet-stream',
            headers={
                'Cache-Control': 'max-age=3600',
                'Access-Control-Allow-Origin': '*'
            }
        )
    except Exception as e:
        print(f"  ❌ 转换失败: {e}")
        return jsonify(ok=False, error=str(e)), 500


@app.route('/models/<path:filename>')
def serve_model(filename):
    """直接提供模型文件下载 (STL 等)"""
    return send_from_directory(MODELS_DIR, filename)


@app.route('/ping')
def ping():
    return jsonify(ok=True)


# ─── 启动 ────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7890))
    print(f"🚀 3D 模型库服务器已启动")
    print(f"📌 http://0.0.0.0:{port}")
    print(f"📁 模型目录: {MODELS_DIR}")
    mf_files = list(Path(MODELS_DIR).rglob('*.3mf'))
    stl_files = list(Path(MODELS_DIR).rglob('*.stl'))
    print(f"📦 发现 {len(mf_files)} 个 3MF, {len(stl_files)} 个 STL")
    app.run(host='0.0.0.0', port=port, debug=False)
