#!/usr/bin/env python3
"""
3D 模型库自动扫描与分类脚本
扫描指定文件夹中的 3D 文件，自动分类并生成/更新 HTML 展示页。
"""

import os
import sys
import re
import json
import zipfile
import base64
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ─── 配置 ───────────────────────────────────────────────
SCAN_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
HTML_TEMPLATE_PATH = sys.argv[2] if len(sys.argv) > 2 else None
OUTPUT_HTML = sys.argv[3] if len(sys.argv) > 3 else "index.html"

EXTENSIONS_3D = {'.stl', '.3mf', '.obj', '.step', '.stp', '.gcode',
                 '.blend', '.fbx', '.ply', '.amf', '.scad', '.f3d'}

# ─── 分类规则 ────────────────────────────────────────────
CATEGORY_RULES = {
    "3D打印工具": {
        "keywords": [
            'bambu', 'p1s', 'p1p', 'a1', 'a1mini', 'k1', 'k1max',
            'hotend', 'fan', 'spool', 'led', 'plate', 'nozzle',
            'extruder', 'filament', 'ams', 'flipper', 'duct',
            'shroud', 'mod', 'upgrade', 'adapter', 'mount',
            'sensor', 'ptfe', 'coupler', 'cooling', 'heater',
            'printer', 'calibrat', 'benchy', 'test', 'tolerance',
            'spool holder', 'bed', 'leveling', 'firmware',
            'painter', 'paint', 'chroma',
        ],
        "icon": "🔧",
    },
    "收纳与支架": {
        "keywords": [
            'box', 'case', 'hook', 'kitchen', 'desk', 'organizer',
            'holder', 'stand', 'shelf', 'rack', 'tray', 'container',
            'bin', 'basket', 'caddy', 'mount', 'bracket', 'hanger',
            'remote', 'phone', 'tablet', 'cable', 'charger',
            'bowl', 'vassoio', 'edge', 'pen', 'pencil', 'cup',
            'storage', 'drawer', 'cabinet', 'slot', 'dock',
            'glasses', 'eyeglass', 'jewelry', 'watch', 'key',
            'wallet', 'lamp', 'light', '灯', '碗', '笔筒',
            '收纳', '托盘', '支架', '架', '盒',
        ],
        "icon": "🗄️",
    },
    "玩具": {
        "keywords": [
            'figure', 'dragon', 'pokemon', 'toy', 'art',
            'statue', 'bust', 'miniature', 'mini', 'doll',
            'action', 'robot', 'mech', 'gundam', 'anime',
            'game', 'chess', 'dice', 'puzzle', 'fidget',
            'spinner', 'gyro', 'car', 'vehicle', 'tank',
            'plane', 'ship', 'rocket', 'sword', 'weapon',
            'cosplay', 'mask', 'helmet', 'armor',
            '陀螺', '爆甲', '小车', '漂移', '玩具',
            'orbit', 'burst', 'gyro',
        ],
        "icon": "🧸",
    },
    "装饰与模型": {
        "keywords": [
            'decor', 'vase', 'sculpture', 'ornament', 'wall',
            'sign', 'plaque', 'lithophane', 'photo', 'frame',
            'flower', 'plant', 'pot', 'garden', 'outdoor',
            'christmas', 'halloween', 'easter', 'holiday',
            'skull', 'skeleton', 'animal', 'cat', 'dog', 'bird',
            'mandala', 'geometric', 'abstract', 'modern',
            '版画', '千夏', '画', '浮雕', '艺术', '摆件',
            '装饰', 'relief', 'print', 'portrait',
        ],
        "icon": "🎨",
    },
}

CAT_ORDER = ["收纳与支架", "3D打印工具", "玩具", "装饰与模型"]
CAT_ICONS = {c: CATEGORY_RULES[c]["icon"] for c in CAT_ORDER}

# ─── 3MF 元数据提取 ──────────────────────────────────────
def extract_3mf_metadata(filepath):
    """从 .3mf 文件提取标题和缩略图"""
    info = {"title": None, "thumbnail": None, "designer": None}
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            names = z.namelist()

            # 1) 提取标题 — 从 3D/3dmodel.model 的 XML metadata
            if '3D/3dmodel.model' in names:
                try:
                    data = z.read('3D/3dmodel.model').decode('utf-8')
                    root = ET.fromstring(data)
                    ns = root.tag.split('}')[0] + '}' if '}' in root.tag else ''
                    for m in root.findall(f'{ns}metadata'):
                        name_attr = m.get('name', '')
                        val = m.text
                        if not val or val in ('None', ''):
                            continue
                        if name_attr == 'Title':
                            info['title'] = val.strip()
                        elif name_attr == 'Designer':
                            info['designer'] = val.strip()
                except Exception:
                    pass

            # 2) 提取缩略图 — 优先级: thumbnail_3mf > plate_1 > top_1
            thumb_candidates = [
                'Auxiliaries/.thumbnails/thumbnail_3mf.png',
                'Auxiliaries/.thumbnails/thumbnail_middle.png',
                'Metadata/plate_1.png',
                'Metadata/top_1.png',
            ]
            for tc in thumb_candidates:
                if tc in names:
                    try:
                        img_data = z.read(tc)
                        b64 = base64.b64encode(img_data).decode('ascii')
                        info['thumbnail'] = f"data:image/png;base64,{b64}"
                        break
                    except Exception:
                        continue
    except Exception:
        pass
    return info


# ─── 文件扫描与物理归并 ──────────────────────────────────
def scan_files(scan_dir):
    """扫描目录，返回按项目分组的文件列表"""
    scan_path = Path(scan_dir).resolve()
    all_files = []

    # 排除部署目录和隐藏目录
    SKIP_DIRS = {'3d-model-library', '.git', 'node_modules', '__pycache__', '.app'}
    for root, dirs, files in os.walk(scan_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith('.app')]
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in EXTENSIONS_3D:
                full = Path(root) / f
                rel = full.relative_to(scan_path)
                all_files.append({
                    "full_path": str(full),
                    "rel_path": str(rel),
                    "filename": f,
                    "ext": ext,
                    "parent_dir": str(Path(root).relative_to(scan_path)) if Path(root) != scan_path else "",
                })

    # 物理归并：按子文件夹分组 或 按文件名前缀分组
    projects = defaultdict(list)

    for fi in all_files:
        if fi["parent_dir"] and fi["parent_dir"] != ".":
            # 子文件夹中的文件归为一个项目
            key = fi["parent_dir"]
        else:
            # 根目录文件：提取前缀来归并
            key = fi["filename"]
        projects[key].append(fi)

    # 对于根目录文件，再尝试前缀归并
    root_files = {}
    subfolder_projects = {}
    for key, files in projects.items():
        if files[0]["parent_dir"] and files[0]["parent_dir"] != ".":
            subfolder_projects[key] = files
        else:
            root_files[key] = files

    # 第一步：合并 "filename (N).ext" 副本到 "filename.ext"
    import re
    dup_pattern = re.compile(r'^(.+?)\s*\(\d+\)(\.\w+)$')
    dup_merge = {}
    for key in list(root_files.keys()):
        m = dup_pattern.match(key)
        if m:
            canonical = m.group(1) + m.group(2)  # e.g. "cat.3mf"
            if canonical not in dup_merge:
                dup_merge[canonical] = canonical
            dup_merge[key] = canonical
        else:
            if key not in dup_merge:
                dup_merge[key] = key
    # 重新分组
    regrouped = defaultdict(list)
    for key, files in root_files.items():
        canonical = dup_merge.get(key, key)
        regrouped[canonical].extend(files)
    root_files = dict(regrouped)

    # 第二步：尝试用前缀归并根目录的文件（共同前缀 >= 4 字符）
    merged = {}
    used = set()
    root_keys = sorted(root_files.keys())
    for i, k1 in enumerate(root_keys):
        if k1 in used:
            continue
        group = [k1]
        base1 = Path(k1).stem.lower()
        for k2 in root_keys[i+1:]:
            if k2 in used:
                continue
            base2 = Path(k2).stem.lower()
            # 找共同前缀
            prefix = os.path.commonprefix([base1, base2])
            if len(prefix) >= 4 and prefix[-1] not in '._- ':
                group.append(k2)
                used.add(k2)
        used.add(k1)
        merge_key = group[0]
        merged[merge_key] = []
        for g in group:
            merged[merge_key].extend(root_files[g])

    # 合并所有项目
    all_projects = {}
    all_projects.update(subfolder_projects)
    all_projects.update(merged)
    return all_projects


# ─── 分类引擎 ────────────────────────────────────────────
def classify_project(project_key, files, metadata_title=None):
    """根据关键词分类项目"""
    # 构建搜索文本
    search_text = project_key.lower()
    for f in files:
        search_text += " " + f["filename"].lower()
    if metadata_title:
        search_text += " " + metadata_title.lower()

    scores = {}
    for cat, rules in CATEGORY_RULES.items():
        score = 0
        for kw in rules["keywords"]:
            if kw.lower() in search_text:
                score += 1
        scores[cat] = score

    # 选得分最高的分类，按 catOrder 优先级排序（收纳 > 玩具 > 装饰 > 工具）
    priority = ["收纳与支架", "玩具", "装饰与模型", "3D打印工具"]
    best_score = max(scores.values())
    if best_score == 0:
        return "装饰与模型"
    # 同分时优先选非"3D打印工具"的分类（避免 LED/lamp 误归）
    tied = [c for c in priority if scores.get(c, 0) == best_score]
    return tied[0] if tied else max(scores, key=scores.get)


# ─── 生成项目标题 ─────────────────────────────────────────
def generate_title(project_key, files, metadata_title=None):
    """生成显示标题"""
    # 优先使用 3mf 元数据中的标题
    if metadata_title and metadata_title not in ('None', '', '[]'):
        return metadata_title

    # 用项目key生成标题
    name = project_key
    # 去掉扩展名
    name = Path(name).stem if '.' in name else name
    # 清理版本号
    name = re.sub(r'[_\-]?v?\d+\.?\d*$', '', name, flags=re.IGNORECASE)
    # 下划线/连字符转空格
    name = re.sub(r'[_\-]+', ' ', name)
    return name.strip() or project_key


# ─── 生成占位缩略图 ──────────────────────────────────────
def generate_placeholder_svg(category, title):
    """生成分类对应的 SVG 占位图（base64）"""
    icon = CAT_ICONS.get(category, "📦")
    colors = {
        "收纳与支架": ("#1a1a3e", "#667eea"),
        "3D打印工具": ("#1a2a1a", "#4ade80"),
        "玩具": ("#2a1a1a", "#f97316"),
        "装饰与模型": ("#1a1a2a", "#a78bfa"),
    }
    bg, accent = colors.get(category, ("#1a1a2a", "#667eea"))
    short_title = title[:12] + "..." if len(title) > 12 else title
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">
  <rect width="400" height="300" fill="{bg}"/>
  <text x="200" y="130" text-anchor="middle" font-size="64">{icon}</text>
  <text x="200" y="190" text-anchor="middle" font-family="Arial" font-size="16" fill="{accent}">{short_title}</text>
  <text x="200" y="220" text-anchor="middle" font-family="Arial" font-size="11" fill="#555">{category}</text>
</svg>'''
    b64 = base64.b64encode(svg.encode('utf-8')).decode('ascii')
    return f"data:image/svg+xml;base64,{b64}"


# ─── 主逻辑 ──────────────────────────────────────────────
def main():
    scan_dir = SCAN_DIR
    print(f"[扫描] 目录: {os.path.abspath(scan_dir)}")

    # 1) 扫描与物理归并
    projects = scan_files(scan_dir)
    print(f"[归并] 发现 {len(projects)} 个模型项目")

    # 2) 读取已有 HTML 中的模型（如果有模板）
    existing_models = []
    existing_files = set()
    html_before_models = ""
    html_after_models = ""

    if HTML_TEMPLATE_PATH and os.path.exists(HTML_TEMPLATE_PATH):
        with open(HTML_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
            html_content = f.read()
        # 提取现有 models 数组
        m = re.search(r'const models = \[(.*?)\];', html_content, re.DOTALL)
        if m:
            try:
                raw = '[' + m.group(1) + ']'
                existing_models = json.loads(raw)
                for em in existing_models:
                    for fn in em.get("files", []):
                        existing_files.add(fn)
                    # 修复旧链接：将分类前缀路径改为实际文件路径
                    old_link = em.get("link", "")
                    if "/" in old_link:
                        # 检查是否是 "分类/项目名/" 格式的虚拟路径
                        parts = old_link.strip("/").split("/", 1)
                        if len(parts) >= 1 and parts[0] in CAT_ORDER:
                            # 虚拟路径，改为实际文件
                            flist = em.get("files", [])
                            if flist:
                                # 检查是否有同名子文件夹
                                first_file = flist[0]
                                scan_p = Path(scan_dir).resolve()
                                # 查找文件实际位置
                                found = False
                                for r, ds, fs in os.walk(scan_p):
                                    if first_file in fs:
                                        rel_dir = Path(r).relative_to(scan_p)
                                        if str(rel_dir) == ".":
                                            em["link"] = first_file
                                        else:
                                            em["link"] = str(rel_dir) + "/"
                                        found = True
                                        break
                                if not found:
                                    em["link"] = first_file if flist else old_link
                print(f"[模板] 已有 {len(existing_models)} 个模型")
            except Exception as e:
                print(f"[警告] 解析模板中的 models 失败: {e}")

    # 3) 处理每个新项目
    new_models = []
    for project_key, files in projects.items():
        # 检查是否已存在
        file_names = [f["filename"] for f in files]
        if all(fn in existing_files for fn in file_names):
            continue  # 跳过已有的

        # 提取 3mf 元数据
        metadata_title = None
        thumbnail = None
        for f in files:
            if f["ext"] == ".3mf":
                meta = extract_3mf_metadata(f["full_path"])
                if meta["title"]:
                    metadata_title = meta["title"]
                if meta["thumbnail"]:
                    thumbnail = meta["thumbnail"]
                break

        # 分类
        category = classify_project(project_key, files, metadata_title)

        # 标题
        title = generate_title(project_key, files, metadata_title)

        # 缩略图
        has_real_thumb = bool(thumbnail)
        if not thumbnail:
            thumbnail = generate_placeholder_svg(category, title)

        # 构建链接 — 指向实际文件/文件夹的相对路径
        if files[0]["parent_dir"] and files[0]["parent_dir"] != ".":
            link = files[0]["parent_dir"] + "/"
        else:
            # 单文件：直接链接到文件本身
            link = files[0]["filename"]

        model_entry = {
            "title": title,
            "category": category,
            "icon": CAT_ICONS.get(category, "📦"),
            "files": file_names,
            "link": link,
            "img": thumbnail,
            "noThumb": not has_real_thumb,
        }
        new_models.append(model_entry)
        print(f"  [新增] {title} → {category}")

    # 4) 合并
    all_models = existing_models + new_models
    print(f"\n[结果] 共 {len(all_models)} 个模型 ({len(new_models)} 个新增)")

    # 5) 生成 HTML
    generate_html(all_models, OUTPUT_HTML, HTML_TEMPLATE_PATH)
    print(f"[输出] HTML 已保存到: {os.path.abspath(OUTPUT_HTML)}")


def generate_html(models, output_path, template_path=None):
    """生成完整的 HTML 文件（始终使用最新模板结构）"""
    models_json = json.dumps(models, ensure_ascii=False, indent=2)
    html = build_full_html(models_json)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def build_full_html(models_json):
    """构建完整 HTML - 赛博朋克 FUI 风格"""
    cat_order_json = json.dumps(CAT_ORDER, ensure_ascii=False)
    cat_icons_json = json.dumps(CAT_ICONS, ensure_ascii=False)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 尝试加载赛博朋克模板
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_file = os.path.join(script_dir, 'cyberpunk_template.html')
    if os.path.exists(template_file):
        with open(template_file, 'r', encoding='utf-8') as f:
            template = f.read()
        html = template.replace('__MODELS_JSON__', models_json)
        html = html.replace('__CAT_ORDER_JSON__', cat_order_json)
        html = html.replace('__CAT_ICONS_JSON__', cat_icons_json)
        html = html.replace('__TIMESTAMP__', timestamp)
        print(f"  [模板] 使用赛博朋克 FUI 模板")
        return html

    # 回退到旧模板
    print(f"  [模板] cyberpunk_template.html 未找到，使用默认模板")
    return f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>3D模型库</title>
<script type="importmap">{{"imports":{{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}}}</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"SF Pro Display","Helvetica Neue",Arial,sans-serif;background:#0d0d1a;color:#e0e0e0;min-height:100vh;padding:40px 24px}}
h1{{text-align:center;font-size:2.2em;margin-bottom:8px;background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-weight:700}}
.sub{{text-align:center;color:#666;margin-bottom:28px;font-size:.92em;transition:opacity .3s}}
.stats{{max-width:1100px;margin:0 auto 24px;display:flex;gap:12px;justify-content:center;flex-wrap:wrap}}
.stat{{background:linear-gradient(135deg,#12122a,#161636);border:1px solid #1e1e3a;border-radius:14px;padding:10px 22px;text-align:center;min-width:90px}}
.stat .num{{font-size:1.5em;font-weight:700;background:linear-gradient(135deg,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.stat .lbl{{font-size:.7em;color:#555;margin-top:2px}}
.toolbar{{max-width:1100px;margin:0 auto 32px;display:flex;gap:12px;justify-content:center;flex-wrap:wrap;align-items:center}}
#searchInput{{padding:11px 20px;border-radius:24px;border:2px solid #1e1e3a;background:#12122a;color:#e0e0e0;width:340px;outline:none;font-size:.92em;transition:all .25s}}
#searchInput:focus{{border-color:#667eea;box-shadow:0 0 20px rgba(102,126,234,.15)}}
#searchInput::placeholder{{color:#444}}
.tags{{display:flex;gap:8px;flex-wrap:wrap;justify-content:center}}
.tag{{padding:7px 16px;border-radius:20px;border:1.5px solid #1e1e3a;background:transparent;color:#666;font-size:.83em;cursor:pointer;transition:all .25s;user-select:none}}
.tag:hover{{border-color:#667eea;color:#c0c0e0}}
.tag.active{{background:linear-gradient(135deg,#667eea,#764ba2);border-color:transparent;color:#fff;font-weight:600;box-shadow:0 4px 16px rgba(102,126,234,.3)}}
.cat-header{{font-size:1.2em;margin:36px auto 14px;padding-left:8px;color:#c0c0e0;font-weight:600;max-width:1100px;display:flex;align-items:center;gap:8px}}
.cat-header .cnt{{font-size:.6em;color:#555;font-weight:400;background:#151528;padding:3px 10px;border-radius:12px;border:1px solid #1e1e3a}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:24px;max-width:1100px;margin:0 auto 8px}}
.card{{text-decoration:none;color:inherit;display:flex;flex-direction:column;cursor:pointer;transition:transform .3s,opacity .3s;opacity:0;animation:fadeIn .5s ease forwards}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:translateY(0)}}}}
.card:hover{{transform:translateY(-8px)}}
.card:hover .body{{box-shadow:0 16px 40px rgba(102,126,234,.3);border-color:#667eea}}
.card:hover .tab{{background:linear-gradient(135deg,#667eea,#764ba2)}}
.card:hover .body img{{transform:scale(1.08)}}
.tab{{width:50%;height:14px;background:#1e1e3a;border-radius:8px 8px 0 0;margin-left:6px;transition:background .3s}}
.body{{background:linear-gradient(135deg,#12122a,#161636);border:2px solid #1e1e3a;border-radius:0 14px 14px 14px;overflow:hidden;aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;transition:all .3s;position:relative}}
.body img{{width:100%;height:100%;object-fit:cover;transition:transform .4s ease}}
.badge{{position:absolute;top:8px;right:8px;background:rgba(102,126,234,.88);color:#fff;font-size:.68em;padding:3px 10px;border-radius:12px;backdrop-filter:blur(6px);font-weight:600}}
.info-btn{{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.55);font-size:.9em;padding:4px 8px;border-radius:10px;backdrop-filter:blur(6px);cursor:pointer;opacity:0;transition:opacity .2s;z-index:2}}
.card:hover .info-btn{{opacity:1}}
.label{{margin-top:10px;font-size:.9em;font-weight:600;color:#d0d0e8;text-align:center;line-height:1.3}}
.file{{font-size:.72em;color:#444;text-align:center;margin-top:3px;padding:0 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}}
.empty{{text-align:center;color:#444;padding:60px 20px;grid-column:1/-1;font-size:1.05em;display:none}}
.empty .eicon{{font-size:3em;margin-bottom:12px;display:block}}
.update-info{{text-align:center;color:#333;font-size:.75em;margin-top:32px}}
.modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);backdrop-filter:blur(8px);z-index:100;justify-content:center;align-items:center}}
.modal-overlay.show{{display:flex}}
.modal{{background:#12122a;border:2px solid #1e1e3a;border-radius:20px;max-width:520px;width:90%;max-height:85vh;overflow-y:auto;padding:0;box-shadow:0 24px 60px rgba(0,0,0,.6)}}
.modal-img{{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:18px 18px 0 0}}
.modal-body{{padding:24px}}
.modal-title{{font-size:1.3em;font-weight:700;color:#e0e0f0;margin-bottom:4px}}
.modal-cat{{font-size:.85em;color:#667eea;margin-bottom:16px}}
.modal-files{{list-style:none;margin:0;padding:0}}
.modal-files li{{padding:8px 12px;margin:4px 0;background:#0d0d1a;border:1px solid #1e1e3a;border-radius:10px;font-size:.85em;color:#aaa;display:flex;align-items:center;gap:8px;cursor:pointer;transition:all .2s}}
.modal-files li:hover{{border-color:#667eea;color:#e0e0e0}}
.modal-files li .ext{{background:#667eea22;color:#667eea;padding:2px 8px;border-radius:6px;font-size:.75em;font-weight:600;flex-shrink:0}}
.modal-file-name{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.modal-file-actions{{display:flex;gap:6px;flex-shrink:0}}
.modal-file-btn{{padding:4px 10px;border-radius:8px;border:1px solid #1e1e3a;background:#0a0a18;color:#aaa;font-size:.75em;cursor:pointer;transition:all .2s;white-space:nowrap}}
.modal-file-btn:hover{{border-color:#667eea;color:#fff}}
.modal-file-btn.preview{{color:#667eea;border-color:#667eea44}}
.modal-file-btn.preview:hover{{background:#667eea22;color:#fff}}
.modal-file-btn.bambu{{color:#4ecdc4;border-color:#4ecdc444}}
.modal-file-btn.bambu:hover{{background:#4ecdc422;color:#fff}}
.modal-close{{position:absolute;top:12px;right:16px;background:rgba(0,0,0,.5);border:none;color:#fff;font-size:1.5em;cursor:pointer;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);transition:background .2s}}
.modal-close:hover{{background:rgba(102,126,234,.6)}}
.toast{{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(80px);background:#667eea;color:#fff;padding:10px 24px;border-radius:12px;font-size:.9em;opacity:0;transition:all .3s ease;z-index:200;pointer-events:none}}
.toast.show{{opacity:1;transform:translateX(-50%) translateY(0)}}
#viewer3d-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);backdrop-filter:blur(12px);z-index:300;justify-content:center;align-items:center;flex-direction:column}}
#viewer3d-overlay.show{{display:flex}}
#viewer3d-box{{width:90vw;max-width:800px;height:70vh;background:#0d0d1a;border:2px solid #1e1e3a;border-radius:20px;overflow:hidden;position:relative}}
#viewer3d-canvas{{width:100%;height:100%;display:block}}
#viewer3d-title{{color:#e0e0f0;font-size:1.1em;font-weight:600;text-align:center;padding:12px 0 6px}}
#viewer3d-close{{position:absolute;top:12px;right:16px;background:rgba(0,0,0,.5);border:none;color:#fff;font-size:1.5em;cursor:pointer;border-radius:50%;width:36px;height:36px;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);z-index:10}}
#viewer3d-close:hover{{background:rgba(102,126,234,.6)}}
#viewer3d-loading{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#667eea;font-size:1.1em;z-index:5}}
#viewer3d-actions{{display:flex;gap:12px;justify-content:center;margin-top:12px}}
#viewer3d-actions button{{padding:10px 24px;border-radius:14px;border:2px solid #1e1e3a;background:#12122a;color:#c0c0e0;font-size:.9em;cursor:pointer;transition:all .25s}}
#viewer3d-actions button:hover{{border-color:#667eea;color:#fff;background:#1a1a3a}}
#viewer3d-actions button.primary{{background:linear-gradient(135deg,#667eea,#764ba2);border-color:transparent;color:#fff;font-weight:600}}
@media(max-width:600px){{body{{padding:24px 12px}}h1{{font-size:1.6em}}#searchInput{{width:100%}}.grid{{grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:16px}}#viewer3d-box{{width:95vw;height:60vh}}}}
</style></head><body>
<h1>📦 3D 模型库</h1>
<p class="sub" id="statusCount"></p>
<div class="stats" id="statsBar"></div>
<div class="toolbar">
  <input type="text" id="searchInput" placeholder="🔍 搜索模型名称或文件名..." oninput="handleSearch()">
  <div class="tags" id="tagContainer"></div>
</div>
<div id="content"></div>
<div class="modal-overlay" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal" style="position:relative">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <img class="modal-img" id="modalImg" src="" alt="">
    <div class="modal-body">
      <div class="modal-title" id="modalTitle"></div>
      <div class="modal-cat" id="modalCat"></div>
      <ul class="modal-files" id="modalFiles"></ul>
      <p style="font-size:.72em;color:#444;margin-top:12px;text-align:center">点击文件预览 3D 模型 · 或在 Bambu Studio 中打开</p>
    </div>
  </div>
</div>
<div class="toast" id="toast">已复制到剪贴板</div>
<div id="viewer3d-overlay" onclick="if(event.target===this)closeViewer()">
  <div id="viewer3d-title"></div>
  <div id="viewer3d-box">
    <button id="viewer3d-close" onclick="closeViewer()">&times;</button>
    <div id="viewer3d-loading">加载中...</div>
    <canvas id="viewer3d-canvas"></canvas>
  </div>
  <div id="viewer3d-actions">
    <button class="primary" id="btn-open-bambu" onclick="openInBambu()">在 Bambu Studio 中打开</button>
    <button onclick="closeViewer()">关闭</button>
  </div>
</div>
<p class="update-info">最后更新: {datetime.now().strftime("%Y-%m-%d %H:%M")} · 通过 <a href="http://127.0.0.1:7890" style="color:#667eea">本地服务器</a> 打开以启用3D预览</p>
<script>
const models = {models_json};
const catOrder={cat_order_json};
const catIcons={cat_icons_json};
let activeCat="all";

function renderStats(d){{
  const tf=d.reduce((s,m)=>s+m.files.length,0);
  const cs=new Set(d.map(m=>m.category));
  document.getElementById('statsBar').innerHTML=`
    <div class="stat"><div class="num">${{d.length}}</div><div class="lbl">模型</div></div>
    <div class="stat"><div class="num">${{tf}}</div><div class="lbl">文件</div></div>
    <div class="stat"><div class="num">${{cs.size}}</div><div class="lbl">分类</div></div>`;
}}

function renderTags(){{
  const c=document.getElementById('tagContainer');
  let h=`<button class="tag ${{activeCat==='all'?'active':''}}" onclick="setCat('all')">全部</button>`;
  catOrder.forEach(cat=>{{
    const n=models.filter(m=>m.category===cat).length;
    h+=`<button class="tag ${{activeCat===cat?'active':''}}" onclick="setCat('${{cat}}')">${{catIcons[cat]}} ${{cat}} (${{n}})</button>`;
  }});
  c.innerHTML=h;
}}

// ─── 3D 查看器 ───
const isServed=location.protocol==='http:'||location.protocol==='https:';
let v3scene,v3camera,v3renderer,v3controls,v3raf,v3currentFile='',v3fromIdx=-1;
const SERVER=location.origin;
let v3libs=null; // 延迟加载 Three.js

async function loadThreeJS(){{
  if(v3libs) return v3libs;
  const THREE=await import('three');
  const {{OrbitControls}}=await import('three/addons/controls/OrbitControls.js');
  const {{STLLoader}}=await import('three/addons/loaders/STLLoader.js');
  v3libs={{THREE,OrbitControls,STLLoader}};
  return v3libs;
}}

function getFilePath(m){{
  const target=m.files.find(f=>f.endsWith('.3mf'))||m.files.find(f=>f.endsWith('.stl'))||m.files[0];
  let fp=target;
  if(m.link.endsWith('/'))fp=m.link+target;
  return fp;
}}

function openFile(idx,e){{
  e.preventDefault();e.stopPropagation();
  const m=models[idx];
  if(m.files.length>1){{
    // 多文件模型: 弹出文件选择器
    showModal(idx,e);
    return;
  }}
  const fp=getFilePath(m);
  if(isServed){{
    showViewer(fp,m.title+' - '+m.files[0]);
  }}else{{
    showModal(idx,e);
  }}
}}

async function showViewer(filePath,title,fromIdx){{
  v3currentFile=filePath;
  v3fromIdx=fromIdx!==undefined?fromIdx:-1;
  const overlay=document.getElementById('viewer3d-overlay');
  const loading=document.getElementById('viewer3d-loading');
  document.getElementById('viewer3d-title').textContent=title;
  loading.textContent='加载 3D 引擎...';
  loading.style.display='flex';
  overlay.classList.add('show');
  document.body.style.overflow='hidden';

  try{{
    const libs=await loadThreeJS();
    const {{THREE,OrbitControls,STLLoader}}=libs;
    const canvas=document.getElementById('viewer3d-canvas');
    const box=document.getElementById('viewer3d-box');

    // 清理旧场景
    if(v3raf)cancelAnimationFrame(v3raf);
    if(v3renderer)v3renderer.dispose();

    // 初始化场景
    v3scene=new THREE.Scene();
    v3scene.background=new THREE.Color(0x0d0d1a);

    const w=box.clientWidth,h=box.clientHeight;
    v3camera=new THREE.PerspectiveCamera(45,w/h,0.1,10000);
    v3renderer=new THREE.WebGLRenderer({{canvas,antialias:true}});
    v3renderer.setSize(w,h);
    v3renderer.setPixelRatio(window.devicePixelRatio);
    v3renderer.outputColorSpace=THREE.SRGBColorSpace;

    v3controls=new OrbitControls(v3camera,canvas);
    v3controls.enableDamping=true;
    v3controls.dampingFactor=0.08;

    // 灯光
    const amb=new THREE.AmbientLight(0xffffff,0.6);
    v3scene.add(amb);
    const dir=new THREE.DirectionalLight(0xffffff,1.0);
    dir.position.set(5,10,7);v3scene.add(dir);
    const dir2=new THREE.DirectionalLight(0x667eea,0.4);
    dir2.position.set(-5,-3,-5);v3scene.add(dir2);

    // 网格地面
    const grid=new THREE.GridHelper(200,40,0x1e1e3a,0x111128);
    v3scene.add(grid);

    loading.textContent='加载模型...';

    // 根据扩展名选择 Loader
    const ext=filePath.split('.').pop().toLowerCase();
    const url=SERVER+'/models/'+encodeURI(filePath);

    if(ext==='3mf'){{
      // 3MF 通过服务器端转换为 STL 加载 (Bambu Studio 3MF 格式兼容)
      const stlUrl=SERVER+'/stl/'+encodeURI(filePath);
      const loader=new STLLoader();
      loader.load(stlUrl,(geometry)=>{{
        const mat=new THREE.MeshPhongMaterial({{color:0x667eea,specular:0x222244,shininess:60}});
        const mesh=new THREE.Mesh(geometry,mat);
        fitAndShow(THREE,mesh);
        loading.style.display='none';
      }},
      (p)=>{{loading.textContent=p.total?`转换加载中 ${{Math.round(p.loaded/p.total*100)}}%`:'正在转换 3MF → STL (大文件可能需要30秒)...'}},
      (err)=>{{loading.textContent='加载失败: '+err.message;console.error(err)}});
    }}else if(ext==='stl'){{
      const loader=new STLLoader();
      loader.load(url,(geometry)=>{{
        const mat=new THREE.MeshPhongMaterial({{color:0x667eea,specular:0x222244,shininess:60}});
        const mesh=new THREE.Mesh(geometry,mat);
        fitAndShow(THREE,mesh);
        loading.style.display='none';
      }},
      (p)=>{{if(p.total)loading.textContent=`加载中 ${{Math.round(p.loaded/p.total*100)}}%`}},
      (err)=>{{loading.textContent='加载失败: '+err.message}});
    }}else{{
      loading.textContent='不支持预览此格式，请在 Bambu Studio 中打开';
    }}

    // 渲染循环
    function animate(){{
      v3raf=requestAnimationFrame(animate);
      v3controls.update();
      v3renderer.render(v3scene,v3camera);
    }}
    animate();

  }}catch(err){{
    loading.textContent='加载失败: '+err.message;
    console.error(err);
  }}
}}

function fitAndShow(THREE,obj){{
  v3scene.add(obj);
  const bbox=new THREE.Box3().setFromObject(obj);
  const center=bbox.getCenter(new THREE.Vector3());
  const size=bbox.getSize(new THREE.Vector3());
  const maxDim=Math.max(size.x,size.y,size.z);
  const scale=maxDim>0?100/maxDim:1;
  obj.scale.setScalar(scale);
  const bbox2=new THREE.Box3().setFromObject(obj);
  const center2=bbox2.getCenter(new THREE.Vector3());
  obj.position.sub(center2);
  v3camera.position.set(80,100,120);
  v3camera.lookAt(0,0,0);
  v3controls.target.set(0,0,0);
  v3controls.update();
}}

function closeViewer(){{
  if(v3raf)cancelAnimationFrame(v3raf);
  if(v3renderer){{v3renderer.dispose();v3renderer=null}}
  v3scene=null;
  document.getElementById('viewer3d-overlay').classList.remove('show');
  document.body.style.overflow='';
  // 多文件模型: 返回文件选择器
  const returnIdx=v3fromIdx;
  v3fromIdx=-1;
  if(returnIdx>=0 && models[returnIdx] && models[returnIdx].files.length>1){{
    showModal(returnIdx);
  }}
}}

function openInBambu(filePath){{
  const fp=filePath||v3currentFile;
  if(!fp)return;
  if(location.protocol==='file:'){{
    // 本地文件模式: 提示启动服务器
    showToast('请运行 python3 3d_server.py 后再试');
    return;
  }}
  if(location.hostname==='127.0.0.1'||location.hostname==='localhost'){{
    // 本地服务器: 调用 /open API
    fetch(SERVER+'/open?file='+encodeURIComponent(fp))
      .then(r=>r.json())
      .then(d=>{{if(d.ok){{showToast('已在 Bambu Studio 中打开')}}else{{showToast('打开失败: '+d.error)}}}})
      .catch(()=>showToast('服务器未响应，请确认 3d_server.py 正在运行'));
    return;
  }}
  // 云端: 下载文件并提示用Bambu Studio打开
  const fn=fp.split('/').pop();
  const url=SERVER+'/models/'+encodeURI(fp);
  window.open(url,'_blank');
  showToast('文件下载中: '+fn+'，请双击用 Bambu Studio 打开');
}}

function showToast(msg){{
  const t=document.getElementById('toast');
  t.textContent=msg;t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2000);
}}

function showModal(idx, e){{
  if(e){{e.preventDefault();e.stopPropagation()}}
  const m=models[idx];
  document.getElementById('modalImg').src=m.img;
  document.getElementById('modalTitle').textContent=m.title;
  document.getElementById('modalCat').textContent=catIcons[m.category]+' '+m.category;
  const fl=document.getElementById('modalFiles');
  fl.innerHTML='';
  const isHttp=isServed;
  m.files.forEach(fn=>{{
    const ext=fn.split('.').pop().toUpperCase();
    const li=document.createElement('li');
    let fp=fn;if(m.link.endsWith('/'))fp=m.link+fn;
    const canPreview=['3MF','STL'].includes(ext);
    let btns='';
    if(isHttp){{
      if(canPreview)btns+=`<button class="modal-file-btn preview" onclick="event.stopPropagation();closeModal();showViewer('${{fp.replace(/'/g,\"\\\\'\")}}','${{(m.title+' - '+fn).replace(/'/g,\"\\\\'\")}}',${{idx}})">🔍 预览</button>`;
      btns+=`<button class="modal-file-btn bambu" onclick="event.stopPropagation();openInBambu('${{fp.replace(/'/g,\"\\\\'\")}}')">🖨️ Bambu</button>`;
    }}else{{
      btns+=`<button class="modal-file-btn copy" onclick="event.stopPropagation();navigator.clipboard.writeText('${{fp.replace(/'/g,\"\\\\'\")}}').then(()=>showToast('已复制: ${{fn}}'))">📋 复制</button>`;
    }}
    li.innerHTML=`<span class="ext">${{ext}}</span><span class="modal-file-name">${{fn}}</span><span class="modal-file-actions">${{btns}}</span>`;
    if(isHttp && canPreview){{
      li.onclick=(ev)=>{{
        if(ev.target.closest('.modal-file-btn'))return;
        closeModal();showViewer(fp,m.title+' - '+fn,idx);
      }};
    }}
    fl.appendChild(li);
  }});
  document.getElementById('modal').classList.add('show');
  document.body.style.overflow='hidden';
}}

function closeModal(){{
  document.getElementById('modal').classList.remove('show');
  document.body.style.overflow='';
}}

document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeModal()}});

function render(data){{
  const ct=document.getElementById('content');
  const st=document.getElementById('statusCount');
  if(!data.length){{ct.innerHTML=`<div class="grid"><div class="empty" style="display:block"><span class="eicon">🍃</span>没有找到匹配的模型</div></div>`;st.textContent='共 0 个模型';renderStats(data);return}}
  st.textContent=`共 ${{data.length}} 个模型 · 点击预览3D模型`;
  renderStats(data);
  const g={{}};catOrder.forEach(c=>{{g[c]=[]}});
  data.forEach(m=>{{if(g[m.category])g[m.category].push(m)}});
  let h='';
  catOrder.forEach(cat=>{{
    const items=g[cat];if(!items||!items.length)return;
    h+=`<div class="cat-header">${{catIcons[cat]}} ${{cat}} <span class="cnt">${{items.length}}</span></div><div class="grid">`;
    items.forEach((it,i)=>{{
      const idx=models.indexOf(it);
      const fd=it.files.length>1?`${{it.files[0]}} 等${{it.files.length}}个文件`:it.files[0];
      const badge=it.files.length>1?`<div class="badge">${{it.files.length}} 文件</div>`:'';
      const infoBtn=it.files.length>1?`<div class="info-btn" onclick="showModal(${{idx}},event)" title="查看全部文件">ℹ️</div>`:'';
      const thumbAttr=it.noThumb?` data-autothumb="${{idx}}"`:' ';
      h+=`<div class="card" onclick="openFile(${{idx}},event)" style="animation-delay:${{i*.06}}s">
        <div class="tab"></div>
        <div class="body"><img src="${{it.img}}" alt="${{it.title}}" loading="lazy" decoding="async"${{thumbAttr}}>${{badge}}${{infoBtn}}</div>
        <div class="label">${{it.title}}</div>
        <div class="file">${{fd}}</div></div>`;
    }});
    h+='</div>';
  }});
  ct.innerHTML=h;
}}

function handleSearch(){{
  const q=document.getElementById('searchInput').value.toLowerCase().trim();
  let f=models;
  if(activeCat!=='all')f=f.filter(m=>m.category===activeCat);
  if(q)f=f.filter(m=>m.title.toLowerCase().includes(q)||m.files.some(fn=>fn.toLowerCase().includes(q))||m.category.toLowerCase().includes(q));
  render(f);
}}

function setCat(c){{activeCat=c;renderTags();handleSearch()}}

// ─── 自动生成 3D 缩略图 ───
async function autoThumbnails(){{
  if(!isServed)return;
  const imgs=document.querySelectorAll('img[data-autothumb]');
  if(!imgs.length)return;

  const libs=await loadThreeJS();
  const {{THREE,STLLoader}}=libs;

  // 离屏渲染器
  const W=400,H=300;
  const renderer=new THREE.WebGLRenderer({{antialias:true,alpha:false}});
  renderer.setSize(W,H);
  renderer.setPixelRatio(1);
  renderer.outputColorSpace=THREE.SRGBColorSpace;

  const scene=new THREE.Scene();
  scene.background=new THREE.Color(0x0d0d1a);
  const camera=new THREE.PerspectiveCamera(40,W/H,0.1,10000);
  const amb=new THREE.AmbientLight(0xffffff,0.6);scene.add(amb);
  const dir=new THREE.DirectionalLight(0xffffff,1.0);dir.position.set(5,10,7);scene.add(dir);
  const dir2=new THREE.DirectionalLight(0x667eea,0.4);dir2.position.set(-5,-3,-5);scene.add(dir2);
  const grid=new THREE.GridHelper(200,40,0x1e1e3a,0x111128);scene.add(grid);

  for(const img of imgs){{
    const idx=parseInt(img.dataset.autothumb);
    const m=models[idx];
    if(!m)continue;

    // 选一个可预览的文件
    const file=m.files.find(f=>f.endsWith('.3mf'))||m.files.find(f=>f.endsWith('.stl'));
    if(!file)continue;

    let fp=file;
    if(m.link.endsWith('/'))fp=m.link+file;
    const ext=file.split('.').pop().toLowerCase();
    const url=ext==='3mf'?SERVER+'/stl/'+encodeURI(fp):SERVER+'/models/'+encodeURI(fp);

    try{{
      const geometry=await new Promise((resolve,reject)=>{{
        const loader=new STLLoader();
        loader.load(url,resolve,undefined,reject);
      }});

      // 清理旧 mesh
      scene.children.forEach(c=>{{if(c.isMesh)scene.remove(c)}});

      const mat=new THREE.MeshPhongMaterial({{color:0x667eea,specular:0x222244,shininess:60}});
      const mesh=new THREE.Mesh(geometry,mat);
      scene.add(mesh);

      // 自适应缩放
      const bbox=new THREE.Box3().setFromObject(mesh);
      const center=bbox.getCenter(new THREE.Vector3());
      const size=bbox.getSize(new THREE.Vector3());
      const maxDim=Math.max(size.x,size.y,size.z);
      const scale=maxDim>0?100/maxDim:1;
      mesh.scale.setScalar(scale);
      const bbox2=new THREE.Box3().setFromObject(mesh);
      const center2=bbox2.getCenter(new THREE.Vector3());
      mesh.position.sub(center2);

      camera.position.set(80,100,120);
      camera.lookAt(0,0,0);

      renderer.render(scene,camera);
      const dataUrl=renderer.domElement.toDataURL('image/png');
      img.src=dataUrl;

      // 清理
      scene.remove(mesh);
      geometry.dispose();
      mat.dispose();
    }}catch(e){{
      console.warn('Auto-thumb failed for',m.title,e);
    }}
  }}
  renderer.dispose();
}}

renderTags();render(models);
// 页面渲染后自动生成缩略图
setTimeout(autoThumbnails,500);
</script>
</body></html>'''


if __name__ == "__main__":
    main()
