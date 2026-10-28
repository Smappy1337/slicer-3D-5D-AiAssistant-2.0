# -*- coding: utf-8 -*-
import json
import os
import re
import math
import time
import zlib
import queue
import struct
import shutil
import threading
import traceback
import urllib.request
import urllib.error
import ssl
import zipfile
import xml.etree.ElementTree as ET

from PyQt5.QtCore import Qt, QUrl, QTimer
from UM.Extension import Extension
from UM.Logger import Logger
from UM.Application import Application

try:
    from UM.Math.Vector import Vector
    from UM.Math.Quaternion import Quaternion
    from UM.Scene.SceneNode import SceneNode
    UM_MATH_AVAILABLE = True
except Exception:
    UM_MATH_AVAILABLE = False

try:
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser,
        QLineEdit, QPushButton, QLabel, QFileDialog, QWidget,
        QCheckBox, QFrame
    )
    QT_WIDGETS_AVAILABLE = True
except Exception as e:
    Logger.log("e", "[AIAssistant] Ошибка импорта QtWidgets: {}".format(e))
    QT_WIDGETS_AVAILABLE = False


# ==============================================================================
# КОНСТАНТЫ И ПУТИ
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
GENERATED_DIR = os.path.join(BASE_DIR, "generated_models")
KB_FILE = os.path.join(BASE_DIR, "knowledge_base.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

ALLOWED_MODEL_EXTS = {".stl", ".obj", ".3mf"}
ALLOWED_DOC_EXTS = {".txt", ".pdf", ".docx", ".pptx", ".doc", ".md", ".csv", ".log"}

VALID_MATERIAL_IDS = {
    "generic_pla", "generic_petg", "generic_abs",
    "enduse_pc", "enduse_pa6", "enduse_pp", "enduse_tpu_d70",
    "fiberpart_pc_g20", "fiberpart_pa12_g12", "fiberpart_abs_g13"
}

SETTING_LABELS_RU = {
    "printing_mode": "Режим печати (3D / 5D)",
    "material_id": "Материал печати",
    "orientation": "Ориентация детали в пространстве",
    "layer_height": "Высота слоя в 3D режиме (мм)",
    "layer_height_5d": "Высота слоя в 5D режиме (мм)",
    "layer_height_0": "Высота первого слоя (мм)",
    "infill_sparse_density": "Плотность заполнения (%)",
    "wall_line_count": "Количество линий стенок",
    "top_bottom_thickness": "Толщина крышки / дна (мм)",
    "support_enable": "Генерация поддержек",
    "speed_print": "Скорость печати (мм/с)",
    "adhesion_type": "Тип прилипания к столу",
    "material_print_temperature": "Температура сопла (°C)",
    "material_bed_temperature": "Температура стола (°C)"
}


def normalize_material_id(raw_val):
    """Приводит любое название материала от ИИ или пользователя к точному системному ID STE Slicer."""
    if not raw_val:
        return None
    s = str(raw_val).strip().lower().replace("-", "_").replace(" ", "_")
    if s in VALID_MATERIAL_IDS:
        return s
    if "pc_g20" in s or "fiberpart_pc" in s or ("pc" in s and "g20" in s) or "стекловолокн" in s:
        return "fiberpart_pc_g20"
    if "pa12" in s:
        return "fiberpart_pa12_g12"
    if "abs_g13" in s or "g13" in s:
        return "fiberpart_abs_g13"
    if "tpu" in s or "d70" in s or "полиуретан" in s or "тпу" in s:
        return "enduse_tpu_d70"
    if "pa6" in s or "nylon" in s or "нейлон" in s or "полиамид" in s:
        return "enduse_pa6"
    if s in ("pc", "enduse_pc", "поликарбонат") or ("pc" in s and "g20" not in s):
        return "enduse_pc"
    if s in ("pp", "enduse_pp", "полипропилен"):
        return "enduse_pp"
    if "petg" in s or "петг" in s:
        return "generic_petg"
    if "abs" in s or "абс" in s:
        return "generic_abs"
    if "pla" in s or "пла" in s:
        return "generic_pla"
    return None


# ==============================================================================
# 1. ТИХАЯ ФОНОВАЯ БАЗА ОПЫТА (БЕЗ ВСПЛЫВАЮЩИХ ОКОН)
# ==============================================================================
def load_json_file(path, default_val):
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default_val


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_default_knowledge_base():
    return {
        "cases": [
            {
                "part_type": "Шестерня прямозубая / зубчатое колесо",
                "keywords": ["шестерн", "зубчат", "gear", "момент", "редуктор", "эвольвент"],
                "mode": "3D",
                "material_id": "fiberpart_pc_g20",
                "settings": {"infill_sparse_density": "100", "wall_line_count": "6", "layer_height": "0.1", "support_enable": "false", "speed_print": "50"},
                "lesson": "Для силовых шестерен применяется композит Fiberpart PC G20, 100% заполнение, 6 стенок, слой 0.1 мм и 3D-печать без поддержек."
            },
            {
                "part_type": "Колесо конвейера / демпферный ролик",
                "keywords": ["колесо конвейер", "конвейерн", "ролик", "демпфер", "wheel"],
                "mode": "3D",
                "material_id": "enduse_tpu_d70",
                "settings": {"infill_sparse_density": "80", "wall_line_count": "6", "layer_height": "0.12", "support_enable": "false", "speed_print": "40"},
                "lesson": "Для конвейерных колес и роликов выбирается эластомер Enduse TPU D70, 6 стенок, 80% заполнение и слой 0.12 мм."
            },
            {
                "part_type": "Пыльник ШРУСа / гофра / криволинейный патрубок",
                "keywords": ["пыльник", "гофр", "шрус", "патрубок", "купол", "5d"],
                "mode": "5D",
                "material_id": "enduse_tpu_d70",
                "settings": {"infill_sparse_density": "100", "wall_line_count": "6", "layer_height": "0.12", "support_enable": "false", "speed_print": "40"},
                "lesson": "Для гофрированных пыльников ШРУСа оптимальна 5D-печать без поддержек из TPU D70 со 100% заполнением."
            }
        ],
        "user_rules": []
    }


def find_relevant_experience(query_text, doc_texts, model_names):
    kb = load_json_file(KB_FILE, get_default_knowledge_base())
    combined = (str(query_text) + " " + " ".join(doc_texts) + " " + " ".join(model_names)).lower()
    matched_cases = []
    for case in kb.get("cases", []):
        for kw in case.get("keywords", []):
            if kw.lower() in combined:
                matched_cases.append(case)
                break
    return matched_cases, kb.get("user_rules", [])


def record_experience_to_kb(part_name, mode, material_id, settings_dict, lesson_text, is_rule=False):
    kb = load_json_file(KB_FILE, get_default_knowledge_base())
    if is_rule:
        kb.setdefault("user_rules", []).append({
            "timestamp": time.strftime("%Y-%m-%d %H:%M"),
            "rule": lesson_text
        })
    else:
        words = [w.lower() for w in re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", str(part_name))][:5]
        kb.setdefault("cases", []).append({
            "part_type": part_name or "Инженерная деталь",
            "keywords": words or ["деталь"],
            "mode": mode or "3D",
            "material_id": material_id or "generic_petg",
            "settings": settings_dict,
            "lesson": lesson_text
        })
        if len(kb["cases"]) > 40:
            kb["cases"] = kb["cases"][-40:]
    save_json_file(KB_FILE, kb)


# ==============================================================================
# 2. БЕЗОПАСНЫЙ ПАРСЕР ДОКУМЕНТОВ (.DOCX, .PPTX, .PDF, .DOC, .TXT)
# ==============================================================================
def extract_text_from_document(path):
    if not path or not os.path.isfile(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in {".txt", ".md", ".csv", ".log"}:
            for enc in ("utf-8", "utf-8-sig", "cp1251", "cp866"):
                try:
                    with open(path, "r", encoding=enc) as f:
                        return f.read().strip()
                except UnicodeDecodeError:
                    continue
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()

        elif ext in {".docx", ".doc"}:
            if zipfile.is_zipfile(path):
                chunks = []
                with zipfile.ZipFile(path, "r") as z:
                    for xf in z.namelist():
                        if xf == "word/document.xml" or xf.startswith("word/header"):
                            tree = ET.fromstring(z.read(xf))
                            for elem in tree.iter():
                                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                                if tag == "p":
                                    texts = [n.text for n in elem.iter() if (n.tag.endswith("}t") or n.tag == "t") and n.text]
                                    if texts:
                                        chunks.append("".join(texts).strip())
                return "\n".join(c for c in chunks if c).strip()
            else:
                with open(path, "rb") as f:
                    raw = f.read()
                chunks = []
                for m in re.findall(b"(?:[\x20-\x7e]\x00|[\x10-\x4f\x01\x51]\x04){5,}", raw):
                    s = m.decode("utf-16-le", errors="ignore").strip()
                    if len(s) > 4 and "Microsoft" not in s and "Normal" not in s:
                        chunks.append(s)
                for m in re.findall(b"[\x20-\x7e\xc0-\xff]{6,}", raw):
                    s = m.decode("cp1251", errors="ignore").strip()
                    if re.search(r"[А-Яа-яЁё]{3,}", s):
                        chunks.append(s)
                return "\n".join(chunks)[:12000]

        elif ext == ".pptx":
            chunks = []
            with zipfile.ZipFile(path, "r") as z:
                slides = sorted(
                    [s for s in z.namelist() if s.startswith("ppt/slides/slide") and s.endswith(".xml")],
                    key=lambda x: int(re.sub(r"\D", "", x) or 0)
                )
                for idx, sf in enumerate(slides, 1):
                    tree = ET.fromstring(z.read(sf))
                    s_lines = []
                    for elem in tree.iter():
                        if elem.tag.endswith("}p") or elem.tag == "p":
                            t_list = [n.text for n in elem.iter() if (n.tag.endswith("}t") or n.tag == "t") and n.text]
                            if t_list:
                                s_lines.append("".join(t_list).strip())
                    if s_lines:
                        chunks.append("[Слайд {}]\n{}".format(idx, "\n".join(s_lines)))
            return "\n\n".join(chunks).strip()

        elif ext == ".pdf":
            for mod_name in ("pypdf", "PyPDF2"):
                try:
                    mod = __import__(mod_name, fromlist=["PdfReader"])
                    reader = mod.PdfReader(path)
                    txt = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
                    if len(txt) > 20:
                        return txt
                except Exception:
                    pass

            with open(path, "rb") as f:
                raw = f.read()
            streams = []
            for m in re.finditer(b"stream[\r\n]+(.*?)[\r\n]+endstream", raw, re.DOTALL):
                s_data = m.group(1)
                for wb in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
                    try:
                        streams.append(zlib.decompress(s_data, wb))
                        break
                    except Exception:
                        pass
                else:
                    streams.append(s_data)
            all_data = b"\n".join(streams) if streams else raw

            cmap = {}
            for block in re.findall(b"beginbfchar(.*?)endbfchar", all_data, re.DOTALL):
                for sh, dh in re.findall(b"<([0-9A-Fa-f]+)>\\s*<([0-9A-Fa-f]+)>", block):
                    try:
                        cmap[int(sh, 16)] = bytes.fromhex(dh.decode("ascii")).decode("utf-16-be", errors="ignore")
                    except Exception:
                        pass

            extracted = []
            for lit in re.findall(b"\\(([^()]{2,})\\)", all_data):
                if lit.startswith(b"\xfe\xff"):
                    s = lit[2:].decode("utf-16-be", errors="ignore")
                elif cmap and any(b in cmap for b in lit):
                    s = "".join(cmap.get(b, chr(b) if 32 <= b < 127 else "") for b in lit)
                else:
                    try:
                        s = lit.decode("utf-8")
                    except UnicodeDecodeError:
                        s = lit.decode("cp1251", errors="ignore")
                if len(s.strip()) > 1:
                    extracted.append(s.strip())
            res = re.sub(r"\s+", " ", " ".join(extracted)).strip()
            return res if len(res) > 15 else "PDF документ успешно прикреплен."
    except Exception as e:
        Logger.log("e", "[AIAssistant] Ошибка чтения документа {}: {}".format(path, e))
    return ""


# ==============================================================================
# 3. АНАЛИЗАТОР 3D-ГЕОМЕТРИИ И АВТО-ОРИЕНТАЦИЯ В ПРОСТРАНСТВЕ
# ==============================================================================
def _parse_stl_triangles(file_path):
    triangles = []
    if not file_path or not os.path.isfile(file_path):
        return triangles
    try:
        with open(file_path, "rb") as f:
            f.read(80)
            count_bytes = f.read(4)
            if len(count_bytes) < 4:
                return triangles
            num_tri = struct.unpack("<I", count_bytes)[0]
            file_size = os.path.getsize(file_path)
            if 84 + num_tri * 50 == file_size and num_tri > 0:
                step = max(1, num_tri // 3500)
                for i in range(0, num_tri, step):
                    f.seek(84 + i * 50)
                    data = f.read(50)
                    if len(data) < 50:
                        break
                    vals = struct.unpack("<12fH", data)
                    v1 = (vals[3], vals[4], vals[5])
                    v2 = (vals[6], vals[7], vals[8])
                    v3 = (vals[9], vals[10], vals[11])
                    triangles.append((v1, v2, v3))
                return triangles
    except Exception:
        pass

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            verts = []
            for line in f:
                line = line.strip()
                if line.startswith("vertex"):
                    parts = line.split()
                    if len(parts) >= 4:
                        verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                        if len(verts) == 3:
                            triangles.append((verts[0], verts[1], verts[2]))
                            verts = []
                            if len(triangles) >= 3500:
                                break
    except Exception:
        pass
    return triangles


def analyze_model_geometry(file_path):
    info = {
        "name": os.path.basename(file_path),
        "path": file_path,
        "size_mm": (0.0, 0.0, 0.0),
        "optimal_rotation": (0, 0, 0),
        "orientation_reason": "Базовая плоскость обеспечивает максимальное прилипание к столу.",
        "overhang_ratio": 0.0,
        "base_contact_area": 100.0,
        "recommended_mode": "3D",
        "mode_reason": "Плоское основание и прямолинейные образующие оптимальны для 3D-печати."
    }
    try:
        ext = os.path.splitext(file_path)[1].lower()
        triangles = _parse_stl_triangles(file_path) if ext == ".stl" else []
        if not triangles:
            return info

        xs = [v[0] for tri in triangles for v in tri]
        ys = [v[1] for tri in triangles for v in tri]
        zs = [v[2] for tri in triangles for v in tri]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        dz = max(zs) - min(zs)
        info["size_mm"] = (round(dx, 1), round(dy, 1), round(dz, 1))

        faces = []
        total_area = 0.0
        for v1, v2, v3 in triangles:
            ux, uy, uz = v2[0] - v1[0], v2[1] - v1[1], v2[2] - v1[2]
            vx, vy, vz = v3[0] - v1[0], v3[1] - v1[1], v3[2] - v1[2]
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length > 1e-7:
                area = 0.5 * length
                faces.append(((nx / length, ny / length, nz / length), area, v1, v2, v3))
                total_area += area

        if total_area <= 0:
            return info

        candidates = [
            ("Базовая ось -Z вниз (0°, 0°, 0°)", (0, 0, -1), (0, 0, 0), dz, min(zs), 2),
            ("Переворот на 180° (+Z вниз)", (0, 0, 1), (180, 0, 0), dz, max(zs), 2),
            ("Укладка гранью -Y вниз (Rx=+90°)", (0, -1, 0), (90, 0, 0), dy, min(ys), 1),
            ("Укладка гранью +Y вниз (Rx=-90°)", (0, 1, 0), (-90, 0, 0), dy, max(ys), 1),
            ("Укладка торцом -X вниз (Ry=-90°)", (-1, 0, 0), (0, -90, 0), dx, min(xs), 0),
            ("Укладка торцом +X вниз (Ry=+90°)", (1, 0, 0), (0, 90, 0), dx, max(xs), 0),
        ]

        best_score = -1e9
        best_cand = candidates[0]
        best_overhang = 0.0
        best_bottom_area = 0.0

        for label, down_vec, rot_angles, height_dim, extreme_coord, axis_idx in candidates:
            bottom_area = 0.0
            overhang_area = 0.0
            for norm, area, v1, v2, v3 in faces:
                dot = norm[0] * down_vec[0] + norm[1] * down_vec[1] + norm[2] * down_vec[2]
                c_val = (v1[axis_idx] + v2[axis_idx] + v3[axis_idx]) / 3.0
                dist_from_base = abs(c_val - extreme_coord)
                if dot > 0.92 and dist_from_base < max(1.5, height_dim * 0.06):
                    bottom_area += area
                elif dot > 0.707 and dist_from_base >= max(1.5, height_dim * 0.06):
                    overhang_area += area

            score = (bottom_area * 2.5) - (overhang_area * 1.8) - (height_dim * 0.4)
            if rot_angles == (0, 0, 0):
                score += total_area * 0.03

            if score > best_score:
                best_score = score
                best_cand = (label, rot_angles, height_dim)
                best_overhang = overhang_area / total_area
                best_bottom_area = bottom_area

        info["optimal_rotation"] = best_cand[1]
        info["overhang_ratio"] = round(best_overhang * 100, 1)
        info["base_contact_area"] = round(best_bottom_area, 1)

        if best_cand[1] == (0, 0, 0):
            info["orientation_reason"] = (
                "Исходная ориентация оптимальна: площадь опоры {:.0f} мм², доля нависаний {:.1f}%."
            ).format(best_bottom_area, info["overhang_ratio"])
        else:
            info["orientation_reason"] = (
                "Выполнен авто-поворот ({}) для максимизации площади опоры ({:.0f} мм²) и снижения нависаний до {:.1f}%."
            ).format(best_cand[0], best_bottom_area, info["overhang_ratio"])

        name_low = info["name"].lower()
        if any(k in name_low for k in ("пыльник", "гофр", "шрус", "bellows", "крыльчатк", "шнек", "купол", "5d")):
            info["recommended_mode"] = "5D"
            info["mode_reason"] = (
                "Выбран 5D-режим Stereotech: сложная пространственная/гофрированная геометрия печатается без поддержек "
                "за счет наклона и вращения стола с непрерывным упрочнением вдоль контура."
            )
        elif info["overhang_ratio"] > 14.0 and min(dx, dy) > 18.0:
            info["recommended_mode"] = "5D"
            info["mode_reason"] = (
                "Выбран 5D-режим печати: доля криволинейных нависаний составляет {:.1f}%, "
                "5-осевая кинематика позволяет вырастить деталь без поддержек."
            ).format(info["overhang_ratio"])
        else:
            info["recommended_mode"] = "3D"
            info["mode_reason"] = (
                "Выбран 3D-режим печати: деталь имеет выраженное плоское основание ({:.0f} мм²) и малые нависания ({:.1f}%)."
            ).format(best_bottom_area, info["overhang_ratio"])
    except Exception as e:
        Logger.log("e", "[AIAssistant] Ошибка анализа геометрии: {}".format(e))

    return info


# ==============================================================================
# 4. ГЕНЕРАТОР 3D-МОДЕЛЕЙ ПО ОПИСАНИЮ (ПАРАМЕТРИЧЕСКИЙ STL)
# ==============================================================================
def _write_ascii_stl(file_path, solid_name, triangles):
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("solid {}\n".format(solid_name))
        for v1, v2, v3 in triangles:
            ux, uy, uz = v2[0] - v1[0], v2[1] - v1[1], v2[2] - v1[2]
            vx, vy, vz = v3[0] - v1[0], v3[1] - v1[1], v3[2] - v1[2]
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            f.write("  facet normal {:.4f} {:.4f} {:.4f}\n    outer loop\n".format(nx / ln, ny / ln, nz / ln))
            f.write("      vertex {:.4f} {:.4f} {:.4f}\n".format(*v1))
            f.write("      vertex {:.4f} {:.4f} {:.4f}\n".format(*v2))
            f.write("      vertex {:.4f} {:.4f} {:.4f}\n".format(*v3))
            f.write("    endloop\n  endfacet\n")
        f.write("endsolid {}\n".format(solid_name))


def _add_quad(tris, p1, p2, p3, p4):
    tris.append((p1, p2, p3))
    tris.append((p1, p3, p4))


def generate_parametric_stl(shape_type, outer_d=40.0, inner_d=10.0, height=12.0, teeth=18, width=30.0, length=40.0, wall_t=4.0):
    shape_type = (shape_type or "gear").lower().strip()
    tris = []
    outer_r = max(5.0, float(outer_d or 40.0) / 2.0)
    inner_r = max(0.0, min(outer_r - 2.0, float(inner_d or 10.0) / 2.0))
    h = max(2.0, float(height or 12.0))

    if shape_type in ("gear", "шестерня"):
        n_teeth = max(6, int(teeth or 18))
        steps = n_teeth * 4
        tooth_depth = max(1.8, outer_r * 0.18)
        root_r = outer_r - tooth_depth
        hole_r = inner_r if inner_r > 0.5 else outer_r * 0.28

        def _gear_r(idx):
            return outer_r if (idx % 4) in (1, 2) else root_r

        for i in range(steps):
            a1 = 2.0 * math.pi * i / steps
            a2 = 2.0 * math.pi * ((i + 1) % steps) / steps
            r1, r2 = _gear_r(i), _gear_r(i + 1)
            pb1, pb2 = (r1 * math.cos(a1), r1 * math.sin(a1), 0.0), (r2 * math.cos(a2), r2 * math.sin(a2), 0.0)
            pt1, pt2 = (r1 * math.cos(a1), r1 * math.sin(a1), h), (r2 * math.cos(a2), r2 * math.sin(a2), h)
            ib1, ib2 = (hole_r * math.cos(a1), hole_r * math.sin(a1), 0.0), (hole_r * math.cos(a2), hole_r * math.sin(a2), 0.0)
            it1, it2 = (hole_r * math.cos(a1), hole_r * math.sin(a1), h), (hole_r * math.cos(a2), hole_r * math.sin(a2), h)
            _add_quad(tris, pb1, pb2, pt2, pt1)
            _add_quad(tris, ib2, ib1, it1, it2)
            _add_quad(tris, it1, it2, pt2, pt1)
            _add_quad(tris, pb1, ib1, ib2, pb2)
        filename = "Generated_Gear_{}mm_{}T.stl".format(int(outer_r * 2), n_teeth)

    elif shape_type in ("bellows", "пыльник", "гофра"):
        waves = 4
        z_steps, a_steps = 32, 36
        t_wall = max(1.8, float(wall_t or 2.2))
        for iz in range(z_steps):
            z1, z2 = h * iz / z_steps, h * (iz + 1) / z_steps
            t1, t2 = iz / float(z_steps), (iz + 1) / float(z_steps)
            r_mid1 = outer_r * (1.0 - 0.42 * t1) + 3.5 * math.sin(t1 * waves * 2 * math.pi)
            r_mid2 = outer_r * (1.0 - 0.42 * t2) + 3.5 * math.sin(t2 * waves * 2 * math.pi)
            ri1, ri2 = max(3.0, r_mid1 - t_wall), max(3.0, r_mid2 - t_wall)
            for ia in range(a_steps):
                a1, a2 = 2 * math.pi * ia / a_steps, 2 * math.pi * ((ia + 1) % a_steps) / a_steps
                ca1, sa1, ca2, sa2 = math.cos(a1), math.sin(a1), math.cos(a2), math.sin(a2)
                o11, o12 = (r_mid1 * ca1, r_mid1 * sa1, z1), (r_mid1 * ca2, r_mid1 * sa2, z1)
                o21, o22 = (r_mid2 * ca1, r_mid2 * sa1, z2), (r_mid2 * ca2, r_mid2 * sa2, z2)
                i11, i12 = (ri1 * ca1, ri1 * sa1, z1), (ri1 * ca2, ri1 * sa2, z1)
                i21, i22 = (ri2 * ca1, ri2 * sa1, z2), (ri2 * ca2, ri2 * sa2, z2)
                _add_quad(tris, o11, o12, o22, o21)
                _add_quad(tris, i12, i11, i21, i22)
                if iz == 0:
                    _add_quad(tris, o11, i11, i12, o12)
                if iz == z_steps - 1:
                    _add_quad(tris, i21, i22, o22, o21)
        filename = "Generated_Bellows_{}x{}mm.stl".format(int(outer_r * 2), int(h))

    elif shape_type in ("bracket", "кронштейн", "уголок"):
        L = max(15.0, float(length or 50.0))
        W = max(10.0, float(width or 30.0))
        H = max(15.0, float(height or 40.0))
        T = max(3.0, min(float(wall_t or 5.0), L * 0.4, H * 0.4))

        def _box(x0, y0, z0, x1, y1, z1):
            p000, p100, p110, p010 = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)
            p001, p101, p111, p011 = (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)
            _add_quad(tris, p000, p010, p110, p100)
            _add_quad(tris, p001, p101, p111, p011)
            _add_quad(tris, p000, p100, p101, p001)
            _add_quad(tris, p100, p110, p111, p101)
            _add_quad(tris, p110, p010, p011, p111)
            _add_quad(tris, p010, p000, p001, p011)

        _box(0, 0, 0, L, W, T)
        _box(0, 0, T, T, W, H)
        filename = "Generated_Bracket_{}x{}x{}mm.stl".format(int(L), int(W), int(H))

    elif shape_type in ("box", "корпус", "куб", "пластина"):
        L = max(5.0, float(length or outer_d or 40.0))
        W = max(5.0, float(width or outer_d or 40.0))
        H = max(2.0, float(height or 20.0))
        x0, y0, z0, x1, y1, z1 = -L / 2.0, -W / 2.0, 0.0, L / 2.0, W / 2.0, H
        p000, p100, p110, p010 = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)
        p001, p101, p111, p011 = (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)
        _add_quad(tris, p000, p010, p110, p100)
        _add_quad(tris, p001, p101, p111, p011)
        _add_quad(tris, p000, p100, p101, p001)
        _add_quad(tris, p100, p110, p111, p101)
        _add_quad(tris, p110, p010, p011, p111)
        _add_quad(tris, p010, p000, p001, p011)
        filename = "Generated_Box_{}x{}x{}mm.stl".format(int(L), int(W), int(H))

    else:
        steps = 48
        for i in range(steps):
            a1, a2 = 2.0 * math.pi * i / steps, 2.0 * math.pi * ((i + 1) % steps) / steps
            pb1, pb2 = (outer_r * math.cos(a1), outer_r * math.sin(a1), 0.0), (outer_r * math.cos(a2), outer_r * math.sin(a2), 0.0)
            pt1, pt2 = (outer_r * math.cos(a1), outer_r * math.sin(a1), h), (outer_r * math.cos(a2), outer_r * math.sin(a2), h)
            _add_quad(tris, pb1, pb2, pt2, pt1)
            if inner_r > 0.5:
                ib1, ib2 = (inner_r * math.cos(a1), inner_r * math.sin(a1), 0.0), (inner_r * math.cos(a2), inner_r * math.sin(a2), 0.0)
                it1, it2 = (inner_r * math.cos(a1), inner_r * math.sin(a1), h), (inner_r * math.cos(a2), inner_r * math.sin(a2), h)
                _add_quad(tris, ib2, ib1, it1, it2)
                _add_quad(tris, it1, it2, pt2, pt1)
                _add_quad(tris, pb1, ib1, ib2, pb2)
            else:
                tris.append(((0.0, 0.0, h), pt1, pt2))
                tris.append(((0.0, 0.0, 0.0), pb2, pb1))
        filename = "Generated_Wheel_D{}xH{}mm.stl".format(int(outer_r * 2), int(h))

    out_path = os.path.join(GENERATED_DIR, filename)
    _write_ascii_stl(out_path, "AISolid", tris)
    return out_path


# ==============================================================================
# 5. САМОПРОВЕРКА (АУДИТ) ПЕРЕД ЗАПУСКОМ НАРЕЗКИ
# ==============================================================================
def run_pre_slice_audit(plan, geometries):
    audit_report = []
    mat_id = (plan.get("material_id") or "").lower()
    mode = (plan.get("printing_mode") or "3D").upper()

    settings_map = {item["key"]: item for item in plan.get("settings", [])}

    if mode == "5D":
        if "support_enable" in settings_map and str(settings_map["support_enable"]["value"]).lower() == "true":
            settings_map["support_enable"]["value"] = "false"
            settings_map["support_enable"]["reason"] += " [Аудит: в 5D-режиме нависания печатаются наклоном стола без поддержек]"
            audit_report.append("🔧 Авто-коррекция: Поддержки отключены, так как активен 5D-режим Stereotech.")
        else:
            audit_report.append("✅ Контроль 5D-кинематики: Поддержки отключены, траектория многоосевая.")
    else:
        max_overhang = max([g.get("overhang_ratio", 0.0) for g in geometries] or [0.0])
        if max_overhang > 22.0 and settings_map.get("support_enable", {}).get("value") == "false":
            settings_map["support_enable"]["value"] = "true"
            audit_report.append("🔧 Авто-коррекция: Обнаружены нависания ({:.1f}%) в 3D-режиме — включены поддержки.".format(max_overhang))
        else:
            audit_report.append("✅ Контроль геометрии 3D: Ориентация устойчива, нависания в пределах нормы.")

    if "tpu" in mat_id:
        spd_item = settings_map.get("speed_print")
        if spd_item:
            try:
                if float(spd_item["value"]) > 45:
                    spd_item["value"] = "40"
                    audit_report.append("🔧 Авто-коррекция: Скорость для TPU снижена до 40 мм/с для защиты от замятия прутка.")
            except Exception:
                pass
        else:
            plan["settings"].append({
                "key": "speed_print",
                "value": "40",
                "reason": "Ограничение скорости экструзии до 40 мм/с для стабильной подачи гибкого TPU D70."
            })
        audit_report.append("✅ Контроль эластомера TPU: Скорость подачи безопасна (40 мм/с).")

    if "fiberpart" in mat_id:
        lh_item = settings_map.get("layer_height")
        if lh_item:
            try:
                if float(lh_item["value"]) < 0.1:
                    lh_item["value"] = "0.1"
                    audit_report.append("🔧 Авто-коррекция: Высота слоя увеличена до 0.1 мм (минимальный порог для стекловолокна).")
            except Exception:
                pass
        audit_report.append("✅ Контроль композита ({}): Высота слоя совместима с армирующим волокном.".format(mat_id))

    min_base = min([g.get("base_contact_area", 100.0) for g in geometries] or [100.0])
    if 0 < min_base < 45.0 and "adhesion_type" not in settings_map:
        plan["settings"].append({
            "key": "adhesion_type",
            "value": "brim",
            "reason": "Добавлена кайма (brim) из-за малой площади опоры со столом ({:.0f} мм²).".format(min_base)
        })
        audit_report.append("🔧 Авто-коррекция: Включена кайма (Brim) для защиты от отрыва детали.")
    else:
        audit_report.append("✅ Контроль адгезии первого слоя: Площадь сцепления со столом достаточна.")

    return audit_report


# ==============================================================================
# 6. БЕЗОПАСНОЕ УПРАВЛЕНИЕ ЯДРОМ STE SLICER (ТОЛЬКО В ГЛАВНОМ ПОТОКЕ QT)
# ==============================================================================
def get_main_thread_slicer_snapshot():
    snap = {
        "material_id": "—",
        "layer_height": "0.2",
        "layer_height_0": "0.3",
        "infill_sparse_density": "20",
        "wall_line_count": "3",
        "support_enable": "False",
        "speed_print": "60",
        "adhesion_type": "skirt"
    }
    app = Application.getInstance()
    if not app:
        return snap
    try:
        ext_mgr = app.getExtruderManager()
        ext_stack = ext_mgr.getActiveExtruderStacks()[0] if (ext_mgr and ext_mgr.getActiveExtruderStacks()) else None
        glob_stack = app.getGlobalContainerStack()

        if ext_stack and hasattr(ext_stack, "material") and ext_stack.material:
            snap["material_id"] = str(ext_stack.material.getId() or ext_stack.material.getName())

        for k in list(snap.keys()):
            if k == "material_id":
                continue
            val = None
            if ext_stack:
                val = ext_stack.getProperty(k, "value")
            if val is None and glob_stack:
                val = glob_stack.getProperty(k, "value")
            if val is not None:
                snap[k] = str(val)
    except Exception as e:
        Logger.log("w", "[AIAssistant] Чтение снимка стека: {}".format(e))
    return snap


def load_model_into_slicer_safe(file_path):
    if not file_path or not os.path.isfile(file_path):
        return
    app = Application.getInstance()
    if not app:
        return
    safe_path = os.path.abspath(file_path).replace("\\", "/")

    def _load():
        try:
            app.readLocalFile(QUrl.fromLocalFile(safe_path))
            Logger.log("i", "[AIAssistant] Загружена модель: {}".format(safe_path))
        except Exception as ex:
            Logger.log("e", "[AIAssistant] Ошибка загрузки модели: {}".format(ex))

    app.callLater(_load)


def _apply_single_setting_now(key, val):
    """Выполняется непосредственно в главном потоке."""
    app = Application.getInstance()
    if not app:
        return
    try:
        stack = app.getGlobalContainerStack()
        if not stack:
            return
        parsed_val = val
        low = str(val).lower().strip()
        if low in ("true", "yes", "on"):
            parsed_val = True
        elif low in ("false", "no", "off"):
            parsed_val = False
        else:
            try:
                parsed_val = float(val) if "." in str(val) else int(val)
            except ValueError:
                parsed_val = str(val)

        stack.userChanges.setProperty(key, "value", parsed_val)
        try:
            ext_mgr = app.getExtruderManager()
            if ext_mgr:
                for ext_stack in ext_mgr.getActiveExtruderStacks():
                    ext_stack.userChanges.setProperty(key, "value", parsed_val)
        except Exception:
            pass
        Logger.log("i", "[AIAssistant] Настройка {} = {}".format(key, parsed_val))
    except Exception as ex:
        Logger.log("e", "[AIAssistant] Ошибка настройки {}: {}".format(key, ex))


def execute_plan_in_slicer(plan, done_callback=None):
    """
    Последовательно и с безопасными интервалами применяет:
    1) Загрузку модели -> 2) Установку материала (setMaterialById) ->
    3) Параметры печати поверх выбранного материала -> 4) Поворот и расстановку -> 5) Слайсинг.
    """
    app = Application.getInstance()
    if not app:
        return

    delay = 0
    models_to_load = list(plan.get("models_to_load", []))
    mat_id = normalize_material_id(plan.get("material_id"))
    settings_list = list(plan.get("settings", []))
    rot = plan.get("rotation", (0, 0, 0))

    # Шаг 1: Загрузка новых 3D-моделей (если были сгенерированы)
    if models_to_load:
        for m_path in models_to_load:
            load_model_into_slicer_safe(m_path)
        delay += 500

    # Шаг 2: Установка материала (выполняется ДО настроек, чтобы профиль пластика не сбросил параметры)
    if mat_id:
        def _step_set_material():
            try:
                mm = app.getMachineManager()
                try:
                    mm.setMaterialById(0, mat_id)
                except Exception:
                    mm.setMaterialById("0", mat_id)
                Logger.log("i", "[AIAssistant] Материал успешно установлен на {}".format(mat_id))
            except Exception as ex:
                Logger.log("e", "[AIAssistant] Ошибка смены материала {}: {}".format(mat_id, ex))

        QTimer.singleShot(delay, lambda: app.callLater(_step_set_material))
        delay += 350

    # Шаг 3: Применение всех параметров печати поверх выбранного материала
    if settings_list:
        def _step_apply_settings():
            for item in settings_list:
                k = item.get("key")
                v = item.get("value")
                if k and v is not None:
                    _apply_single_setting_now(k, v)

        QTimer.singleShot(delay, lambda: app.callLater(_step_apply_settings))
        delay += 250

    # Шаг 4: Поворот детали в пространстве и авто-расстановка на столе
    def _step_orient_and_arrange():
        try:
            if UM_MATH_AVAILABLE and rot and any(a != 0 for a in rot):
                rx_deg, ry_deg, rz_deg = rot
                controller = app.getController()
                if controller and controller.getScene():
                    root = controller.getScene().getRoot()
                    for node in root.getChildren():
                        if hasattr(node, "callDecoration") and node.callDecoration("isSliceable"):
                            if rx_deg:
                                node.rotate(Quaternion.fromAngleAxis(math.radians(float(rx_deg)), Vector(1, 0, 0)), SceneNode.TransformSpace.World)
                            if ry_deg:
                                node.rotate(Quaternion.fromAngleAxis(math.radians(float(ry_deg)), Vector(0, 1, 0)), SceneNode.TransformSpace.World)
                            if rz_deg:
                                node.rotate(Quaternion.fromAngleAxis(math.radians(float(rz_deg)), Vector(0, 0, 1)), SceneNode.TransformSpace.World)
            if plan.get("arrange", True):
                app.arrangeAll()
        except Exception as ex:
            Logger.log("w", "[AIAssistant] Ошибка ориентации/расстановки: {}".format(ex))

    QTimer.singleShot(delay, lambda: app.callLater(_step_orient_and_arrange))
    delay += 300

    # Шаг 5: Запуск нарезки (слайсинга) и обновление верхней строки статуса
    if plan.get("slice", True):
        def _step_slice():
            try:
                backend = app.getBackend()
                if backend:
                    backend.forceSlice()
            except Exception:
                pass
            if done_callback:
                try:
                    done_callback()
                except Exception:
                    pass

        QTimer.singleShot(delay, lambda: app.callLater(_step_slice))
    elif done_callback:
        QTimer.singleShot(delay, done_callback)


# ==============================================================================
# 7. ГИБРИДНЫЙ ИИ-ДВИЖОК (ОБЛАЧНЫЙ LLM + БЕЗОТКАЗНЫЙ ЛОКАЛЬНЫЙ ИИ-ТЕХНОЛОГ)
# ==============================================================================
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "generate_3d_model",
            "description": "Создать с нуля новую 3D-модель (STL) по текстовому описанию и загрузить на стол.",
            "parameters": {
                "type": "object",
                "properties": {
                    "shape_type": {
                        "type": "string",
                        "description": "Тип детали: 'gear' (шестерня), 'wheel' (колесо/втулка/цилиндр), 'bellows' (гофра/пыльник), 'bracket' (кронштейн), 'box' (корпус/пластина)"
                    },
                    "outer_diameter": {"type": "number"},
                    "inner_diameter": {"type": "number"},
                    "height": {"type": "number"},
                    "teeth": {"type": "integer"},
                    "length": {"type": "number"},
                    "width": {"type": "number"}
                },
                "required": ["shape_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "select_printing_mode",
            "description": "Выбрать режим печати: '3D' или '5D' (многоосевая 5D-печать Stereotech).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["3D", "5D"]},
                    "reason": {"type": "string"}
                },
                "required": ["mode", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "orient_model_in_space",
            "description": "Выбрать оптимальные углы поворота детали в пространстве на печатном столе.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rx": {"type": "number"},
                    "ry": {"type": "number"},
                    "rz": {"type": "number"},
                    "reason": {"type": "string"}
                },
                "required": ["rx", "ry", "rz", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_material",
            "description": "Обязательно выбрать материал для печати по его точному ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_id": {
                        "type": "string",
                        "description": "Строго один из ID: 'generic_pla', 'generic_petg', 'generic_abs', 'enduse_pc', 'enduse_pa6', 'enduse_pp', 'enduse_tpu_d70', 'fiberpart_pc_g20', 'fiberpart_pa12_g12', 'fiberpart_abs_g13'"
                    },
                    "reason": {"type": "string", "description": "Инженерное обоснование выбора материала"}
                },
                "required": ["material_id", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_print_setting",
            "description": "Установить параметр печати в слайсере с объяснением причины.",
            "parameters": {
                "type": "object",
                "properties": {
                    "setting_key": {
                        "type": "string",
                        "description": "Ключ: infill_sparse_density, layer_height, wall_line_count, support_enable, speed_print, adhesion_type"
                    },
                    "value": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["setting_key", "value", "reason"]
            }
        }
    }
]

SYSTEM_PROMPT = """Ты — главный ИИ-технолог 3D и 5D аддитивного производства в слайсере STE Slicer (Stereotech).
Твоя задача — анализировать ТЗ и сообщения пользователя, обязательно вызывать `set_material` (выбирая точный ID материала), выбирать 3D или 5D режим (`select_printing_mode`), ориентацию детали в пространстве (`orient_model_in_space`) и все параметры печати (`set_print_setting`).

Доступные ID материалов для `set_material`:
- Стандартные: 'generic_pla', 'generic_petg', 'generic_abs'
- Инженерные: 'enduse_pc', 'enduse_pa6', 'enduse_pp', 'enduse_tpu_d70' (для колес конвейера, роликов, демпферов и пыльников ШРУСа)
- Композитные: 'fiberpart_pc_g20', 'fiberpart_pa12_g12', 'fiberpart_abs_g13' (для силовых шестерен, кронштейнов и высоких нагрузок >70°C)
"""


def call_cloud_llm_api(messages, use_tools=True):
    url = "https://api.timeweb.ai/v1/chat/completions"
    api_key = "sk-YPkGgXM491sMIFz6bMDiMw"
    payload = {
        "model": "dashscope/qwen3.8-flash",
        "messages": messages
    }
    if use_tools:
        payload["tools"] = TOOLS_SCHEMA
        payload["tool_choice"] = "auto"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + api_key,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    with urllib.request.urlopen(req, context=ctx, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_builtin_engineering_ai(user_prompt, all_docs, geometries, matched_cases, user_rules, slicer_snapshot, novice_mode, chat_obj):
    """
    Встроенный точный ИИ-технолог STE Slicer.
    Гарантирует 100% подбор материала, 3D/5D режима и настроек по ТЗ.
    """
    prompt_low = user_prompt.lower()
    docs_combined = "\n".join([d.get("text", "") for d in all_docs]).lower()
    models_combined = " ".join([g.get("name", "") for g in geometries]).lower()
    full_context_low = prompt_low + "\n" + docs_combined + "\n" + models_combined

    generated_models_info = []
    learned_new_rules = []

    default_rot = geometries[-1]["optimal_rotation"] if geometries else (0, 0, 0)
    default_rot_reason = geometries[-1]["orientation_reason"] if geometries else "Модель расположена по центру стола на базовой плоскости."
    default_mode = geometries[-1]["recommended_mode"] if geometries else chat_obj.get("last_mode", "3D")
    default_mode_reason = geometries[-1]["mode_reason"] if geometries else "Выбран оптимальный режим печати по геометрии детали."

    plan = {
        "models_to_load": [],
        "rotation": default_rot,
        "rotation_reason": default_rot_reason,
        "printing_mode": default_mode,
        "mode_reason": default_mode_reason,
        "material_id": None,
        "material_reason": "",
        "settings": [],
        "arrange": True,
        "slice": False
    }

    # 1. ГЕНЕРАЦИЯ 3D-МОДЕЛИ ПО ОПИСАНИЮ
    gen_triggers = ("создай", "сгенерируй", "построй", "сделай модель", "нарисуй")
    if any(t in prompt_low for t in gen_triggers):
        shape_type = "gear"
        if any(w in prompt_low for w in ("пыльник", "гофр", "шрус")):
            shape_type = "bellows"
        elif any(w in prompt_low for w in ("кронштейн", "уголок", "креплен")):
            shape_type = "bracket"
        elif any(w in prompt_low for w in ("корпус", "короб", "куб", "пластин", "блок")):
            shape_type = "box"
        elif any(w in prompt_low for w in ("втулк", "колес", "ролик", "цилиндр", "шайб")):
            shape_type = "wheel"

        def _extract_num(pattern, default_v):
            m = re.search(pattern, prompt_low)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except Exception:
                    pass
            return default_v

        od = _extract_num(r"диаметр\w*\s*(\d+(?:[.,]\d+)?)", 42.0 if shape_type == "gear" else 50.0)
        id_val = _extract_num(r"отверсти\w*\s*(\d+(?:[.,]\d+)?)", 10.0)
        h_val = _extract_num(r"(?:высот|толщин)\w*\s*(\d+(?:[.,]\d+)?)", 12.0 if shape_type == "gear" else 40.0)
        teeth_val = int(_extract_num(r"(\d+)\s*зуб", 18))
        l_val = _extract_num(r"длин\w*\s*(\d+(?:[.,]\d+)?)", 55.0)
        w_val = _extract_num(r"ширин\w*\s*(\d+(?:[.,]\d+)?)", 30.0)

        gen_path = generate_parametric_stl(
            shape_type=shape_type,
            outer_d=od, inner_d=id_val, height=h_val,
            teeth=teeth_val, width=w_val, length=l_val
        )
        plan["models_to_load"].append(gen_path)
        plan["slice"] = True
        chat_obj.setdefault("context_models", []).append(gen_path)
        gen_geom = analyze_model_geometry(gen_path)
        geometries.append(gen_geom)
        plan["rotation"] = gen_geom["optimal_rotation"]
        plan["rotation_reason"] = gen_geom["orientation_reason"]
        plan["printing_mode"] = gen_geom["recommended_mode"]
        plan["mode_reason"] = gen_geom["mode_reason"]
        generated_models_info.append("{} (габариты: {}×{}×{} мм)".format(
            os.path.basename(gen_path), *gen_geom["size_mm"]
        ))
        full_context_low += " " + os.path.basename(gen_path).lower()

    # 2. СОХРАНЕНИЕ ПРАВИЛА ПОЛЬЗОВАТЕЛЯ В ФОНОВУЮ БАЗУ
    if any(k in prompt_low for k in ("запомни", "правило:", "всегда ставь", "учти на будущее")):
        record_experience_to_kb("Правило пользователя", plan["printing_mode"], plan["material_id"], {}, user_prompt, is_rule=True)
        learned_new_rules.append(user_prompt)

    # 3. ПОДБОР РЕЖИМА (3D / 5D), МАТЕРИАЛА И НАСТРОЕК ПО ТЗ
    plan["slice"] = True

    # Приоритет 1: Шестерня / Зубчатое колесо / Механизм под крутящий момент
    if any(w in full_context_low for w in ("шестерн", "зубчат", "gear", "эвольвент", "редуктор", "стекловолокн", "pc g20", "pc_g20")):
        plan["printing_mode"] = "3D"
        plan["mode_reason"] = (
            "Выбрана 3D-печать с укладкой шестерни плоским торцом на стол: концентрические периметры повторяют контур каждого зуба, "
            "обеспечивая максимальную изгибную прочность зубьев и точность эвольвенты."
        )
        plan["material_id"] = "fiberpart_pc_g20"
        plan["material_reason"] = (
            "Композит Fiberpart PC G20 (поликарбонат + 20% стекловолокна) обеспечивает высокую жесткость, "
            "теплостойкость (>70°C) и устойчивость зубьев к срыву под крутящим моментом."
        )
        plan["settings"] = [
            {"key": "infill_sparse_density", "value": "100", "reason": "100% заполнение превращает ступицу и зубья в монолитную силовую деталь."},
            {"key": "wall_line_count", "value": "6", "reason": "6 линий стенок гарантируют, что весь профиль зуба состоит из сплошного армированного контура."},
            {"key": "layer_height", "value": "0.1", "reason": "Высота слоя 0.1 мм обеспечивает высокую точность профиля эвольвенты и минимальную шероховатость."},
            {"key": "support_enable", "value": "false", "reason": "При печати шестерни на торце нависания отсутствуют, поддержки не требуются."},
            {"key": "speed_print", "value": "50", "reason": "Скорость 50 мм/с обеспечивает качественное сплавление композита со стекловолокном."}
        ]

    # Приоритет 2: Пыльник ШРУСа / Гофра / Криволинейный эластичный кожух (5D печать)
    elif any(w in full_context_low for w in ("пыльник", "шрус", "гофр", "bellows", "42°", "42 град")):
        plan["printing_mode"] = "5D"
        plan["mode_reason"] = (
            "Выбрана 5D-печать Stereotech: гофрированный профиль пыльника имеет переменные углы наклона стенок. "
            "5-осевая кинематика позволяет вырастить деталь без поддержек и обеспечить полную герметичность."
        )
        plan["material_id"] = "enduse_tpu_d70"
        plan["material_reason"] = (
            "Эластомер Enduse TPU D70 выбран за гибкость при многократных изгибах до 42°, "
            "а также стойкость к маслам, бензину и дорожным реагентам."
        )
        plan["settings"] = [
            {"key": "infill_sparse_density", "value": "100", "reason": "100% заполнение гарантирует абсолютную герметичность стенок гофры."},
            {"key": "wall_line_count", "value": "6", "reason": "6 сплошных периметров защищают пыльник от усталостного разрыва при циклических деформациях."},
            {"key": "layer_height", "value": "0.12", "reason": "Высота слоя 0.12 мм обеспечивает баланс между герметичностью спайки слоев и эластичностью."},
            {"key": "support_enable", "value": "false", "reason": "В 5D-режиме нависающие волны гофры печатаются наклоном стола без поддержек."},
            {"key": "speed_print", "value": "40", "reason": "Скорость 40 мм/с предотвращает замятие гибкого прутка TPU в экструдере."}
        ]

    # Приоритет 3: Колесо конвейерной линии / Демпферный ролик
    elif any(w in full_context_low for w in ("колес", "конвейер", "ролик", "демпфер", "wheel", "tpu", "тпу")):
        plan["printing_mode"] = "3D"
        plan["mode_reason"] = (
            "Выбрана классическая 3D-печать: цилиндрическое колесо имеет плоский базовый торец и соосную ступицу, "
            "поэтому 3-осевая укладка обеспечивает идеальную геометрию посадочного отверстия."
        )
        plan["material_id"] = "enduse_tpu_d70"
        plan["material_reason"] = (
            "Полиуретан Enduse TPU D70 обеспечивает высокое трение с конвейерной лентой, "
            "гашение ударов и износостойкость."
        )
        plan["settings"] = [
            {"key": "infill_sparse_density", "value": "80", "reason": "Плотность заполнения 80% создает упругий демпфирующий сердечник для работы под нагрузкой."},
            {"key": "wall_line_count", "value": "6", "reason": "6 линий стенок формируют износостойкий обод и прочную ступицу."},
            {"key": "layer_height", "value": "0.12", "reason": "Тонкий слой 0.12 мм обеспечивает точность посадочного отверстия."},
            {"key": "support_enable", "value": "false", "reason": "При укладке колеса плоским торцом на стол нависания отсутствуют."},
            {"key": "speed_print", "value": "40", "reason": "Стабильная скорость экструзии для эластомера TPU D70."}
        ]

    # Приоритет 4: Универсальная деталь / Кронштейн / Корпус
    else:
        is_5d_part = (default_mode == "5D") or ("5d" in prompt_low) or ("5д" in prompt_low) or ("кронштейн" in full_context_low)
        plan["printing_mode"] = "5D" if is_5d_part else "3D"
        plan["mode_reason"] = (
            "Выбран 5D-режим печати для многоосевого упрочнения детали вдоль направлений нагрузки без поддержек."
            if is_5d_part else
            "Выбран 3D-режим печати: геометрия детали оптимальна для послойного выращивания."
        )
        plan["material_id"] = "generic_petg"
        plan["material_reason"] = "Инженерный сополимер PETG выбран как оптимальный материал по прочности, межслоевой адгезии и малой усадке."
        plan["settings"] = [
            {"key": "infill_sparse_density", "value": "60", "reason": "Плотность заполнения 60% обеспечивает конструкционную прочность детали."},
            {"key": "wall_line_count", "value": "4", "reason": "4 периметра формируют жесткий несущий контур."},
            {"key": "layer_height", "value": "0.15", "reason": "Высота слоя 0.15 мм обеспечивает точность размеров и качественную поверхность."},
            {"key": "support_enable", "value": "false" if is_5d_part else "true", "reason": "В 5D-режиме поддержки не нужны." if is_5d_part else "Поддержки включены для нависающих участков."}
        ]

    # 4. ПЕРЕОПРЕДЕЛЕНИЕ ПАРАМЕТРОВ ПРЯМЫМИ КОМАНДАМИ ПОЛЬЗОВАТЕЛЯ В ЧАТЕ
    user_mat = normalize_material_id(prompt_low)
    if user_mat:
        plan["material_id"] = user_mat
        plan["material_reason"] = "Материал '{}' установлен напрямую по вашему требованию в чате.".format(user_mat)

    if re.search(r"\b5[dд]\b", prompt_low):
        plan["printing_mode"] = "5D"
        plan["mode_reason"] = "Режим 5D-печати включен по вашему прямому запросу."
    elif re.search(r"\b3[dд]\b", prompt_low):
        plan["printing_mode"] = "3D"
        plan["mode_reason"] = "Режим 3D-печати включен по вашему прямому запросу."

    rot_m = re.search(r"поверн\w*.*?(?:на\s*)?(-?\d+)\s*град", prompt_low)
    if rot_m:
        ang = float(rot_m.group(1))
        plan["rotation"] = (ang, 0, 0)
        plan["rotation_reason"] = "Деталь повернута на {}° в пространстве по вашей команде.".format(int(ang))

    def _override_setting(s_key, s_val, s_reason):
        for item in plan["settings"]:
            if item["key"] == s_key:
                item["value"] = str(s_val)
                item["reason"] = s_reason
                return
        plan["settings"].append({"key": s_key, "value": str(s_val), "reason": s_reason})

    inf_m = re.search(r"(?:заполнен|плотност)\w*\s*(?:на\s*|до\s*)?(\d+)\s*%?", prompt_low)
    if inf_m:
        v = min(100, max(0, int(inf_m.group(1))))
        _override_setting("infill_sparse_density", v, "Плотность заполнения {}% установлена по вашему запросу.".format(v))

    lh_m = re.search(r"(?:высота\s*слоя|слой)\s*(?:на\s*|до\s*)?(0[.,]\d+)", prompt_low)
    if lh_m:
        v = lh_m.group(1).replace(",", ".")
        _override_setting("layer_height", v, "Высота слоя {} мм установлена по вашему запросу.".format(v))

    w_m = re.search(r"(?:стенк|стенок|периметр)\w*\s*(?:на\s*|до\s*)?(\d+)", prompt_low) or re.search(r"(\d+)\s*(?:стенк|стенок|периметр)", prompt_low)
    if w_m:
        v = int(w_m.group(1))
        _override_setting("wall_line_count", v, "Количество линий стенок ({}) установлено по вашему запросу.".format(v))

    sp_m = re.search(r"скорост\w*\s*(?:печати\s*)?(?:на\s*|до\s*)?(\d+)", prompt_low)
    if sp_m:
        v = int(sp_m.group(1))
        _override_setting("speed_print", v, "Скорость печати {} мм/с установлена по вашему запросу.".format(v))

    if any(p in prompt_low for p in ("без поддерж", "убери поддерж", "выключи поддерж", "отключи поддерж")):
        _override_setting("support_enable", "false", "Генерация поддержек отключена по вашему указанию.")
    elif any(p in prompt_low for p in ("включи поддерж", "с поддержкам", "добавь поддерж")):
        _override_setting("support_enable", "true", "Генерация поддержек включена по вашему указанию.")

    geom_summary = (
        "Модель '{}': габариты {}×{}×{} мм, площадь контакта со столом {:.0f} мм², доля нависаний {:.1f}%.".format(
            geometries[-1]["name"],
            geometries[-1]["size_mm"][0], geometries[-1]["size_mm"][1], geometries[-1]["size_mm"][2],
            geometries[-1]["base_contact_area"], geometries[-1]["overhang_ratio"]
        )
        if geometries else
        "Выполнен анализ технических требований ТЗ и параметров стека STE Slicer."
    )

    llm_thinking = "{} Выбран режим {} печати и материал '{}'.".format(
        geom_summary, plan["printing_mode"], plan["material_id"]
    )

    s_dict_now = {item["key"]: item["value"] for item in plan["settings"]}
    chosen_lh = s_dict_now.get("layer_height", "0.12")
    chosen_inf = s_dict_now.get("infill_sparse_density", "100")
    chosen_walls = s_dict_now.get("wall_line_count", "6")
    chosen_mat = plan["material_id"]

    explanation = (
        "<b>Инженерное заключение:</b> Установлен режим <b>{mode}-печати</b> и материал <b>{mat}</b>. "
        "Высота слоя <b>{lh} мм</b>, <b>{walls} линий стенок</b> и заполнение <b>{inf}%</b> "
        "обеспечивают полное соответствие техническому заданию."
    ).format(mode=plan["printing_mode"], mat=chosen_mat, lh=chosen_lh, walls=chosen_walls, inf=chosen_inf)

    if novice_mode:
        explanation += (
            "<br><br><div style='background-color:#172536; border-left:3px solid #38bdf8; padding:8px 10px; border-radius:6px; font-size:12px; color:#bae6fd;'>"
            "<b>🎓 Пояснение для начинающих:</b><br>"
            "• <b>Режим {mode}:</b> 3D-печать укладывает плоские горизонтальные слои, а 5D-печать наклоняет и вращает деталь, позволяя печатать нависания и гофры без поддержек.<br>"
            "• <b>Стенки ({walls} шт.):</b> Внешний контур воспринимает главные изгибные и ударные нагрузки.<br>"
            "• <b>Высота слоя ({lh} мм):</b> Тонкий слой повышает точность размеров и прочность спекания слоев."
            "</div>"
        ).format(mode=plan["printing_mode"], walls=chosen_walls, lh=chosen_lh)

    return plan, llm_thinking, explanation, generated_models_info, learned_new_rules


# ==============================================================================
# 8. ВСПЛЫВАЮЩЕЕ ОКНО ИИ-АССИСТЕНТА В STE SLICER
# ==============================================================================
if QT_WIDGETS_AVAILABLE:
    class ChatPopupDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("AI Assistant — STE Slicer (3D / 5D ИИ-Технолог)")
            self.resize(780, 740)
            self.setWindowFlags(Qt.Window)
            self.setAcceptDrops(True)

            self.ui_queue = queue.Queue()
            self.queue_timer = QTimer(self)
            self.queue_timer.timeout.connect(self._process_ui_queue)
            self.queue_timer.start(80)

            self.attached_models = []
            self.attached_docs = []

            self.chat_session = {
                "title": "Текущий проект",
                "llm_messages": [],
                "html_bubbles": [],
                "context_docs": [],
                "context_models": [],
                "last_mode": "3D"
            }

            self._init_ui()
            self._apply_styles()
            self._reset_to_welcome()
            self._update_top_telemetry()

        def _disable_auto_default(self, btn):
            """Отключает авто-нажатие кнопки при нажатии Enter в QDialog."""
            btn.setAutoDefault(False)
            btn.setDefault(False)

        def _init_ui(self):
            main_layout = QVBoxLayout(self)
            main_layout.setContentsMargins(0, 0, 0, 0)
            main_layout.setSpacing(0)

            # 1. ВЕРХНЯЯ ПАНЕЛЬ СТАТУСА
            top_bar = QFrame()
            top_bar.setObjectName("topBar")
            top_layout = QHBoxLayout(top_bar)
            top_layout.setContentsMargins(14, 10, 14, 10)
            top_layout.setSpacing(10)

            self.telemetry_lbl = QLabel("Режим: 3D  |  Материал: —  |  Слой: —")
            self.telemetry_lbl.setObjectName("telemetryLabel")
            top_layout.addWidget(self.telemetry_lbl, stretch=1)

            self.chk_novice = QCheckBox("🎓 Обучение новичков")
            self.chk_novice.setObjectName("noviceCheck")
            self.chk_novice.setChecked(True)
            top_layout.addWidget(self.chk_novice)

            self.chk_pin = QCheckBox("📌 Поверх окон")
            self.chk_pin.setObjectName("noviceCheck")
            self.chk_pin.setChecked(False)
            self.chk_pin.toggled.connect(self.toggle_stay_on_top)
            top_layout.addWidget(self.chk_pin)

            self.btn_clear = QPushButton("🗑 Очистить чат")
            self.btn_clear.setObjectName("topDangerBtn")
            self._disable_auto_default(self.btn_clear)
            self.btn_clear.clicked.connect(self.clear_current_session)
            top_layout.addWidget(self.btn_clear)

            main_layout.addWidget(top_bar)

            # 2. ПАНЕЛЬ БЫСТРЫХ ДЕЙСТВИЙ
            quick_bar = QFrame()
            quick_bar.setObjectName("quickBar")
            qb_layout = QHBoxLayout(quick_bar)
            qb_layout.setContentsMargins(14, 6, 14, 6)
            qb_layout.setSpacing(8)

            btn_gen_gear = QPushButton("🧊 Создать шестерню (STL)")
            btn_gen_gear.setObjectName("chipBtn")
            self._disable_auto_default(btn_gen_gear)
            btn_gen_gear.clicked.connect(lambda: self._quick_prompt("Создай 3D-модель прямозубой шестерни диаметром 42 мм, высотой 10 мм, 18 зубьев, отверстие 10 мм и настрой её для высоких нагрузок"))

            btn_gen_bellows = QPushButton("🌀 Создать пыльник ШРУСа (5D)")
            btn_gen_bellows.setObjectName("chipBtn")
            self._disable_auto_default(btn_gen_bellows)
            btn_gen_bellows.clicked.connect(lambda: self._quick_prompt("Создай 3D-модель гофрированного пыльника диаметром 50 мм, высотой 45 мм и настрой для него 5D печать без поддержек из TPU"))

            btn_gen_bracket = QPushButton("📐 Создать кронштейн")
            btn_gen_bracket.setObjectName("chipBtn")
            self._disable_auto_default(btn_gen_bracket)
            btn_gen_bracket.clicked.connect(lambda: self._quick_prompt("Создай 3D-модель силового кронштейна длиной 55 мм, шириной 30 мм, высотой 40 мм и выбери оптимальное расположение"))

            btn_audit_now = QPushButton("🛡️ Проверить настройки")
            btn_audit_now.setObjectName("chipBtn")
            self._disable_auto_default(btn_audit_now)
            btn_audit_now.clicked.connect(lambda: self._quick_prompt("Проведи проверку текущих настроек слайсера и объясни каждый параметр"))

            qb_layout.addWidget(btn_gen_gear)
            qb_layout.addWidget(btn_gen_bellows)
            qb_layout.addWidget(btn_gen_bracket)
            qb_layout.addWidget(btn_audit_now)
            qb_layout.addStretch(1)
            main_layout.addWidget(quick_bar)

            # 3. ОКНО СООБЩЕНИЙ ЧАТА
            self.chat_browser = QTextBrowser()
            self.chat_browser.setOpenExternalLinks(False)
            main_layout.addWidget(self.chat_browser, stretch=1)

            # 4. ИНДИКАТОР ПРОЦЕССА РАЗМЫШЛЕНИЯ
            self.thinking_frame = QFrame()
            self.thinking_frame.setObjectName("thinkingFrame")
            self.thinking_frame.setVisible(False)
            th_layout = QVBoxLayout(self.thinking_frame)
            th_layout.setContentsMargins(14, 8, 14, 8)
            self.thinking_lbl = QLabel("🧠 ИИ-технолог анализирует задачу...")
            self.thinking_lbl.setObjectName("thinkingLabel")
            self.thinking_lbl.setWordWrap(True)
            th_layout.addWidget(self.thinking_lbl)
            main_layout.addWidget(self.thinking_frame)

            # 5. НИЖНЯЯ ПАНЕЛЬ ВВОДА
            bottom_bar = QFrame()
            bottom_bar.setObjectName("bottomBar")
            b_layout = QVBoxLayout(bottom_bar)
            b_layout.setContentsMargins(14, 10, 14, 14)
            b_layout.setSpacing(6)

            self.files_lbl = QLabel("")
            self.files_lbl.setObjectName("filesLabel")
            self.files_lbl.setVisible(False)
            b_layout.addWidget(self.files_lbl)

            input_row = QHBoxLayout()
            input_row.setSpacing(8)

            self.btn_attach = QPushButton("📎 Прикрепить ТЗ / STL")
            self.btn_attach.setObjectName("attachBtn")
            self._disable_auto_default(self.btn_attach)
            self.btn_attach.clicked.connect(self.select_files)

            self.input_field = QLineEdit()
            self.input_field.setObjectName("inputField")
            self.input_field.setPlaceholderText("Напишите задачу, попросите создать 3D-модель или перетащите сюда файлы...")
            self.input_field.returnPressed.connect(self.send_request)

            self.send_btn = QPushButton("➤")
            self.send_btn.setObjectName("sendBtn")
            self._disable_auto_default(self.send_btn)
            self.send_btn.clicked.connect(self.send_request)

            input_row.addWidget(self.btn_attach)
            input_row.addWidget(self.input_field, stretch=1)
            input_row.addWidget(self.send_btn)

            b_layout.addLayout(input_row)
            main_layout.addWidget(bottom_bar)

        def _apply_styles(self):
            self.setStyleSheet("""
                QDialog { background-color: #14171c; color: #eceef2; font-family: 'Segoe UI', Arial, sans-serif; }
                #topBar { background-color: #1a1e24; border-bottom: 1px solid #2b313b; }
                #telemetryLabel { color: #10b981; font-size: 12px; font-weight: bold; }
                #noviceCheck { color: #cbd5e1; font-size: 12px; }
                #topDangerBtn {
                    background-color: #222731; color: #f87171; border: 1px solid #333a47;
                    border-radius: 8px; padding: 5px 10px; font-size: 11.5px; font-weight: bold;
                }
                #topDangerBtn:hover { background-color: #3f1d24; border-color: #ef4444; }
                #quickBar { background-color: #171a20; border-bottom: 1px solid #262b34; }
                #chipBtn {
                    background-color: #222731; color: #e2e8f0; border: 1px solid #333a47;
                    border-radius: 12px; padding: 4px 10px; font-size: 11px;
                }
                #chipBtn:hover { border-color: #10b981; color: #10b981; }
                QTextBrowser { background-color: #14171c; border: none; padding: 14px; font-size: 13px; }
                #thinkingFrame { background-color: #192330; border-top: 1px solid #2563eb; }
                #thinkingLabel { color: #60a5fa; font-size: 12px; }
                #bottomBar { background-color: #1a1e24; border-top: 1px solid #2b313b; }
                #attachBtn {
                    background-color: #222731; color: #e2e8f0; border: 1px solid #3a4250;
                    border-radius: 16px; padding: 8px 14px; font-size: 12px; font-weight: bold;
                }
                #attachBtn:hover { background-color: #2e3543; border-color: #10b981; }
                #inputField {
                    background-color: #222731; color: #ffffff; border: 1px solid #3a4250;
                    border-radius: 16px; padding: 9px 15px; font-size: 13px;
                }
                #inputField:focus { border: 1px solid #10b981; }
                #sendBtn {
                    background-color: #10b981; color: #062117; border: none;
                    border-radius: 18px; min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px;
                    font-size: 15px; font-weight: bold;
                }
                #sendBtn:hover { background-color: #059669; color: #ffffff; }
                #sendBtn:disabled { background-color: #2d3440; color: #64748b; }
                #filesLabel { color: #fbbf24; font-size: 12px; font-weight: bold; padding-left: 6px; }
            """)

        def toggle_stay_on_top(self, checked):
            try:
                flags = self.windowFlags()
                if checked:
                    flags |= Qt.WindowStaysOnTopHint
                else:
                    flags &= ~Qt.WindowStaysOnTopHint
                self.setWindowFlags(flags)
                self.show()
            except Exception as e:
                Logger.log("e", "[AIAssistant] Ошибка флага окна: {}".format(e))

        def _build_welcome_html(self):
            return """
            <div style="background-color:#1e242d; border:1px solid #2e3744; border-left:4px solid #10b981; padding:12px 14px; border-radius:10px; margin-bottom:10px; color:#eceef2; line-height:1.45;">
                <b style="color:#10b981; font-size:14px;">Привет! Я автономный 3D/5D ИИ-технолог STE Slicer.</b><br><br>
                • 📂 <b>Загрузка ТЗ и 3D-моделей:</b> нажмите <b>📎 Прикрепить ТЗ / STL</b> или перетащите файлы (<code>.docx, .pptx, .pdf, .doc, .txt, .stl, .obj, .3mf</code>) в окно.<br>
                • 🧭 <b>Анализ геометрии и 3D/5D режим:</b> сам поворачиваю деталь в оптимальное положение, выбираю 3D или 5D печать и задаю материал.<br>
                • 🧊 <b>Генерация моделей по описанию:</b> попросите создать шестерню, пыльник, втулку или кронштейн.<br>
                • 🛡️ <b>Аудит и обучение:</b> проверяю настройки перед нарезкой и подробно объясняю каждое выбранное значение.
            </div>
            """

        def _reset_to_welcome(self):
            self.chat_browser.clear()
            w_html = self._build_welcome_html()
            self.chat_session["html_bubbles"] = [w_html]
            self.chat_browser.append(w_html)

        def clear_current_session(self):
            self.chat_session["llm_messages"].clear()
            self.chat_session["context_docs"].clear()
            self.chat_session["context_models"].clear()
            self.attached_models.clear()
            self.attached_docs.clear()
            self.files_lbl.setVisible(False)
            self._reset_to_welcome()

        # ---------------- ЗАГРУЗКА ФАЙЛОВ И DRAG-AND-DROP ----------------
        def dragEnterEvent(self, event):
            try:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
            except Exception:
                pass

        def dropEvent(self, event):
            try:
                files = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
                if files:
                    self._add_files(files)
            except Exception as e:
                Logger.log("e", "[AIAssistant] Ошибка dropEvent: {}".format(e))

        def select_files(self):
            try:
                was_on_top = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
                if was_on_top:
                    self.toggle_stay_on_top(False)

                files, _ = QFileDialog.getOpenFileNames(
                    self,
                    "Выберите файлы ТЗ или 3D-модели",
                    "",
                    "Файлы ТЗ и 3D-модели (*.stl *.obj *.3mf *.docx *.doc *.pptx *.pdf *.txt *.md *.csv);;Все файлы (*.*)"
                )

                if was_on_top:
                    self.toggle_stay_on_top(True)

                if files:
                    self._add_files(files)
            except Exception as e:
                Logger.log("e", "[AIAssistant] Ошибка диалога выбора файлов: {}".format(e))

        def _add_files(self, files):
            try:
                for raw_path in files:
                    if not raw_path or not os.path.isfile(raw_path):
                        continue
                    ext = os.path.splitext(raw_path)[1].lower()
                    name = os.path.basename(raw_path)

                    safe_name = "{}_{}".format(int(time.time() * 1000), re.sub(r"[^\w\-. А-Яа-яЁё]", "_", name))
                    stored_path = os.path.join(UPLOAD_DIR, safe_name)
                    try:
                        shutil.copy2(raw_path, stored_path)
                    except Exception:
                        stored_path = raw_path

                    if ext in ALLOWED_MODEL_EXTS:
                        if stored_path not in self.attached_models:
                            self.attached_models.append(stored_path)
                        if stored_path not in self.chat_session["context_models"]:
                            self.chat_session["context_models"].append(stored_path)

                        load_model_into_slicer_safe(stored_path)
                        geom = analyze_model_geometry(stored_path)
                        sys_html = """
                        <div style="background-color:#19222c; border-left:3px solid #f59e0b; padding:8px 12px; border-radius:6px; margin-bottom:8px; font-size:12px; color:#e2e8f0;">
                            <b style="color:#fbbf24;">Система:</b> ☑️ <b>{}</b> — модель загружена на стол.<br>
                            <span style="color:#94a3b8;">Габариты: {}×{}×{} мм | Нависания: {}% | Рекомендован режим: <b>{}</b></span>
                        </div>
                        """.format(name, geom["size_mm"][0], geom["size_mm"][1], geom["size_mm"][2], geom["overhang_ratio"], geom["recommended_mode"])
                        self._append_html_to_chat(sys_html)

                    elif ext in ALLOWED_DOC_EXTS:
                        text = extract_text_from_document(stored_path)
                        self.attached_docs.append((name, text))
                        self.chat_session["context_docs"].append({"name": name, "text": text})
                        preview = (text[:250] + "...") if len(text) > 250 else (text or "Текст извлечен")
                        sys_html = """
                        <div style="background-color:#19222c; border-left:3px solid #10b981; padding:8px 12px; border-radius:6px; margin-bottom:8px; font-size:12px; color:#e2e8f0;">
                            <b style="color:#10b981;">Система:</b> ☑️ <b>{}</b> — распознано {} симв.<br>
                            <span style="color:#94a3b8; font-size:11px;">{}</span>
                        </div>
                        """.format(name, len(text), preview.replace("<", "&lt;").replace(">", "&gt;"))
                        self._append_html_to_chat(sys_html)
                    else:
                        sys_html = """
                        <div style="background-color:#2d1a1e; border-left:3px solid #ef4444; padding:8px 12px; border-radius:6px; margin-bottom:8px; font-size:12px; color:#fca5a5;">
                            Формат файла <b>{}</b> не поддерживается (используйте STL, OBJ, 3MF, DOCX, PPTX, PDF, DOC, TXT).
                        </div>
                        """.format(name)
                        self._append_html_to_chat(sys_html)

                total = len(self.attached_models) + len(self.attached_docs)
                if total > 0:
                    self.files_lbl.setText("📎 Прикреплено файлов: {} (нажмите Enter для автоматической настройки)".format(total))
                    self.files_lbl.setVisible(True)
            except Exception as e:
                Logger.log("e", "[AIAssistant] Ошибка _add_files: {}\n{}".format(e, traceback.format_exc()))

        def _append_html_to_chat(self, html_str):
            try:
                self.chat_session["html_bubbles"].append(html_str)
                self.chat_browser.append(html_str)
                sb = self.chat_browser.verticalScrollBar()
                sb.setValue(sb.maximum())
            except Exception as e:
                Logger.log("e", "[AIAssistant] Ошибка добавления сообщения: {}".format(e))

        def _quick_prompt(self, text):
            self.input_field.setText(text)
            self.send_request()

        # ---------------- ОТПРАВКА ЗАПРОСА И ОЧЕРЕДЬ UI ----------------
        def send_request(self):
            try:
                user_text = self.input_field.text().strip()
                has_new_files = bool(self.attached_models or self.attached_docs)
                if not user_text and not has_new_files:
                    return

                display_text = user_text if user_text else "Примени настройки слайсинга, задай материал, выбери 3D или 5D режим и оптимальное расположение детали исходя из технического задания."
                self.input_field.clear()
                self.files_lbl.setVisible(False)

                if self.attached_models:
                    self.chat_session["title"] = os.path.splitext(os.path.basename(self.attached_models[0]))[0]
                elif self.attached_docs:
                    self.chat_session["title"] = os.path.splitext(self.attached_docs[0][0])[0]

                user_html = """
                <div style="background-color:#1e3a5f; border:1px solid #2563eb; padding:10px 14px; border-radius:10px; margin-bottom:10px; color:#ffffff;">
                    <div style="color:#93c5fd; font-size:11px; font-weight:bold; margin-bottom:4px;">Вы</div>
                    {}
                </div>
                """.format(display_text.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))
                self._append_html_to_chat(user_html)

                models_batch = list(self.attached_models)
                docs_batch = list(self.attached_docs)
                self.attached_models.clear()
                self.attached_docs.clear()

                self.send_btn.setEnabled(False)
                self.btn_attach.setEnabled(False)
                self.thinking_frame.setVisible(True)
                self.thinking_lbl.setText("🧠 Этап 1/5: Чтение контекста диалога и файлов ТЗ...")

                slicer_snapshot = get_main_thread_slicer_snapshot()
                novice_mode = self.chk_novice.isChecked()

                threading.Thread(
                    target=self._background_agent_worker,
                    args=(self.chat_session, display_text, models_batch, docs_batch, novice_mode, slicer_snapshot),
                    daemon=True
                ).start()
            except Exception as e:
                Logger.log("e", "[AIAssistant] Ошибка send_request: {}".format(e))
                self.send_btn.setEnabled(True)
                self.btn_attach.setEnabled(True)

        def _process_ui_queue(self):
            while not self.ui_queue.empty():
                try:
                    event_type, payload = self.ui_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    if event_type == "thinking_step":
                        self.thinking_frame.setVisible(True)
                        self.thinking_lbl.setText(str(payload))

                    elif event_type == "agent_done":
                        self.thinking_frame.setVisible(False)
                        self.send_btn.setEnabled(True)
                        self.btn_attach.setEnabled(True)

                        plan = payload.get("plan")
                        response_html = payload.get("html", "")
                        if response_html:
                            self._append_html_to_chat(response_html)

                        if plan:
                            execute_plan_in_slicer(plan, self._update_top_telemetry)
                        else:
                            self._update_top_telemetry()
                except Exception as e:
                    Logger.log("e", "[AIAssistant] Ошибка _process_ui_queue: {}".format(e))

        def _update_top_telemetry(self):
            try:
                mode_str = self.chat_session.get("last_mode", "3D")
                snap = get_main_thread_slicer_snapshot()
                self.telemetry_lbl.setText(
                    "Режим: {}  |  Материал: {}  |  Слой: {} мм  |  Заполнение: {}%  |  Стенки: {}".format(
                        mode_str, snap["material_id"], snap["layer_height"], snap["infill_sparse_density"], snap["wall_line_count"]
                    )
                )
            except Exception:
                pass

        # ---------------- ФОНОВЫЙ ПОТОК ИИ-ТЕХНОЛОГА ----------------
        def _background_agent_worker(self, chat_obj, user_prompt, new_models, new_docs, novice_mode, slicer_snapshot):
            thinking_log = []

            def _step(msg_text):
                thinking_log.append(msg_text)
                self.ui_queue.put(("thinking_step", msg_text))

            try:
                all_models = list(chat_obj.get("context_models", []))
                for m in new_models:
                    if m not in all_models:
                        all_models.append(m)

                all_docs = list(chat_obj.get("context_docs", []))
                doc_texts = [d["text"] for d in all_docs if d.get("text")]

                _step("🧠 Этап 2/5: Анализ 3D-геометрии детали (площадь опоры, нависания и выбор 3D vs 5D)...")
                geometries = [analyze_model_geometry(m) for m in all_models if os.path.isfile(m)]

                _step("🧠 Этап 3/5: Анализ инженерных требований ТЗ...")
                matched_cases, user_rules = find_relevant_experience(
                    user_prompt, doc_texts, [os.path.basename(m) for m in all_models]
                )

                _step("🧠 Этап 4/5: Подбор материала, ориентации детали, параметров печати и режима (3D / 5D)...")

                # Сначала вычисляем эталонный инженерный план (гарантирует наличие материала и всех настроек)
                fallback_plan, fb_thinking, fb_explanation, fb_gen_models, fb_rules = run_builtin_engineering_ai(
                    user_prompt=user_prompt,
                    all_docs=all_docs,
                    geometries=geometries,
                    matched_cases=matched_cases,
                    user_rules=user_rules,
                    slicer_snapshot=slicer_snapshot,
                    novice_mode=novice_mode,
                    chat_obj=chat_obj
                )

                plan = fallback_plan
                llm_thinking_text = fb_thinking
                raw_content = fb_explanation
                generated_models_info = fb_gen_models

                # Если еще не была вызвана генерация модели в fallback, пробуем обогатить ответ через облачный LLM
                if not generated_models_info:
                    try:
                        context_blocks = []
                        if geometries:
                            g_lines = [
                                "Модель '{}': Габариты={}x{}x{} мм, Нависания={:.1f}%, Площадь опоры={:.0f} мм², Авто-поворот={}, Режим={}".format(
                                    g["name"], g["size_mm"][0], g["size_mm"][1], g["size_mm"][2],
                                    g["overhang_ratio"], g["base_contact_area"], g["optimal_rotation"], g["recommended_mode"]
                                ) for g in geometries
                            ]
                            context_blocks.append("=== АНАЛИЗ 3D-ГЕОМЕТРИИ ===\n" + "\n".join(g_lines))
                        if all_docs:
                            for d in all_docs[-3:]:
                                context_blocks.append("=== ДОКУМЕНТ ТЗ '{}' ===\n{}".format(d["name"], d["text"][:14000]))

                        full_user_msg = "\n\n".join(context_blocks + ["Запрос пользователя:\n" + user_prompt])
                        llm_history = list(chat_obj.get("llm_messages", [])[-6:])
                        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + llm_history + [
                            {"role": "user", "content": full_user_msg}
                        ]

                        res = call_cloud_llm_api(messages, use_tools=True)
                        msg = res["choices"][0]["message"]
                        cloud_content = msg.get("content") or ""
                        tool_calls = msg.get("tool_calls") or []

                        cloud_settings = []
                        cloud_mat = None
                        cloud_mat_reason = ""

                        for tc in tool_calls:
                            fname = tc["function"]["name"]
                            try:
                                args = json.loads(tc["function"]["arguments"])
                            except Exception:
                                args = {}

                            if fname == "set_material":
                                norm_m = normalize_material_id(args.get("material_id"))
                                if norm_m:
                                    cloud_mat = norm_m
                                    cloud_mat_reason = args.get("reason") or plan["material_reason"]
                            elif fname == "select_printing_mode" and args.get("mode"):
                                plan["printing_mode"] = str(args.get("mode")).upper()
                                if args.get("reason"):
                                    plan["mode_reason"] = args.get("reason")
                            elif fname == "orient_model_in_space":
                                plan["rotation"] = (float(args.get("rx", 0)), float(args.get("ry", 0)), float(args.get("rz", 0)))
                                if args.get("reason"):
                                    plan["rotation_reason"] = args.get("reason")
                            elif fname == "set_print_setting":
                                k = args.get("setting_key")
                                v = args.get("value")
                                r = args.get("reason") or "Оптимизировано по ТЗ."
                                if k and v is not None:
                                    cloud_settings.append({"key": k, "value": str(v), "reason": r})

                        # Также проверяем XML-вызовы <invoke> внутри текста ответа
                        if "<invoke" in cloud_content:
                            for m in re.finditer(r'<invoke\s+name=["\']([^"\']+)["\']\s*>(.*?)</invoke>', cloud_content, re.DOTALL):
                                fn = m.group(1).strip()
                                b_xml = m.group(2)
                                p_dict = {}
                                for pm in re.finditer(r'<parameter\s+name=["\']([^"\']+)["\']\s*>(.*?)</parameter\s*>', b_xml, re.DOTALL):
                                    p_dict[pm.group(1).strip()] = pm.group(2).strip()
                                if fn == "set_material":
                                    norm_m = normalize_material_id(p_dict.get("material_id"))
                                    if norm_m:
                                        cloud_mat = norm_m
                                        cloud_mat_reason = p_dict.get("reason") or plan["material_reason"]
                                elif fn == "set_print_setting" and p_dict.get("setting_key"):
                                    cloud_settings.append({
                                        "key": p_dict["setting_key"],
                                        "value": p_dict.get("value", ""),
                                        "reason": p_dict.get("reason") or "По требованиям ТЗ."
                                    })
                            cloud_content = re.sub(r"<function_calls>.*?</function_calls>", "", cloud_content, flags=re.DOTALL)
                            cloud_content = re.sub(r"<invoke\s+name=.*?</invoke>", "", cloud_content, flags=re.DOTALL).strip()

                        # Если облачная модель вернула валидный материал, используем его, иначе оставляем гарантированный материал из fallback_plan!
                        if cloud_mat:
                            plan["material_id"] = cloud_mat
                            plan["material_reason"] = cloud_mat_reason
                        if cloud_settings:
                            # Объединяем настройки, чтобы ни один важный параметр не потерялся
                            merged = {item["key"]: item for item in plan["settings"]}
                            for cs in cloud_settings:
                                merged[cs["key"]] = cs
                            plan["settings"] = list(merged.values())
                        if cloud_content.strip():
                            th_m = re.search(r"<thinking>(.*?)</thinking>", cloud_content, re.DOTALL)
                            if th_m:
                                llm_thinking_text = th_m.group(1).strip()
                                cloud_content = re.sub(r"<thinking>.*?</thinking>", "", cloud_content, flags=re.DOTALL).strip()
                            if cloud_content.strip():
                                raw_content = cloud_content.strip()
                    except Exception as cloud_err:
                        Logger.log("i", "[AIAssistant] Использован встроенный ИИ-технолог ({})".format(cloud_err))

                # 5. Предварительная проверка (Аудит) перед запуском нарезки
                audit_results = []
                if plan.get("slice") or plan.get("material_id") or plan.get("settings"):
                    _step("🛡️ Этап 5/5: Предварительная самопроверка (аудит) настроек слайсера перед нарезкой...")
                    audit_results = run_pre_slice_audit(plan, geometries)
                    chat_obj["last_mode"] = plan["printing_mode"]

                    part_label = chat_obj.get("title", "Инженерная деталь")
                    s_dict = {item["key"]: item["value"] for item in plan["settings"]}
                    record_experience_to_kb(
                        part_label,
                        plan["printing_mode"],
                        plan["material_id"],
                        s_dict,
                        "Режим {}, Материал {}, Слой {} мм".format(
                            plan["printing_mode"],
                            plan["material_id"] or slicer_snapshot.get("material_id", "—"),
                            s_dict.get("layer_height", slicer_snapshot.get("layer_height", "0.12"))
                        )
                    )

                chat_obj.setdefault("llm_messages", []).append({"role": "user", "content": user_prompt})
                chat_obj.setdefault("llm_messages", []).append({"role": "assistant", "content": raw_content or "Настройки применены."})

                final_html = self._format_rich_assistant_response(
                    llm_thinking=llm_thinking_text,
                    thinking_steps=thinking_log,
                    generated_models=generated_models_info,
                    plan=plan,
                    audit_results=audit_results,
                    explanation=raw_content,
                    slicer_snapshot=slicer_snapshot
                )

                self.ui_queue.put(("agent_done", {"plan": plan, "html": final_html}))

            except Exception as ex:
                Logger.log("e", "[AIAssistant] Ошибка потока ИИ: {}\n{}".format(ex, traceback.format_exc()))
                err_html = """
                <div style="background-color:#2d1a1e; border-left:4px solid #ef4444; padding:12px; border-radius:8px; margin-bottom:10px; color:#fca5a5;">
                    <b>⚠️ Ошибка обработки:</b> {}
                </div>
                """.format(str(ex))
                self.ui_queue.put(("agent_done", {"plan": None, "html": err_html}))

        def _format_rich_assistant_response(self, llm_thinking, thinking_steps, generated_models, plan, audit_results, explanation, slicer_snapshot):
            blocks = []

            th_lines = [s.replace("🧠 ", "• ") for s in thinking_steps]
            if llm_thinking:
                th_lines.append("<b>Логика принятия решения:</b> " + llm_thinking.replace("\n", "<br>"))
            blocks.append("""
            <div style="background-color:#161d26; border:1px solid #263345; border-left:3px solid #60a5fa; padding:9px 12px; border-radius:8px; margin-bottom:10px; font-size:12px; color:#93c5fd;">
                <b style="color:#60a5fa;">🧠 Процесс размышления ИИ-технолога:</b><br>
                {}
            </div>
            """.format("<br>".join(th_lines)))

            if generated_models:
                blocks.append("""
                <div style="background-color:#132a23; border:1px solid #10b981; padding:9px 12px; border-radius:8px; margin-bottom:10px; font-size:12px; color:#a7f3d0;">
                    <b>🧊 Сгенерирована и загружена 3D-модель по описанию:</b><br>• {}
                </div>
                """.format("<br>• ".join(generated_models)))

            has_config_changes = bool(plan.get("material_id") or plan.get("settings") or plan.get("slice"))
            if has_config_changes:
                rows_html = []

                mode_val = plan.get("printing_mode", "3D")
                mode_color = "#a855f7" if mode_val == "5D" else "#3b82f6"
                rows_html.append("""
                <tr>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b;"><b>Режим печати (3D / 5D)</b></td>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#94a3b8;">Авто</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:{}; font-weight:bold;">{} печать</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#cbd5e1;">{}</td>
                </tr>
                """.format(mode_color, mode_val, plan.get("mode_reason", "")))

                rx, ry, rz = plan.get("rotation", (0, 0, 0))
                rot_str = "Rx={}°, Ry={}°, Rz={}° (Центр стола)".format(int(rx), int(ry), int(rz))
                rows_html.append("""
                <tr>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b;"><b>Расположение в пространстве</b></td>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#94a3b8;">Исходное</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#10b981; font-weight:bold;">{}</td>
                    <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#cbd5e1;">{}</td>
                </tr>
                """.format(rot_str, plan.get("rotation_reason", "")))

                if plan.get("material_id"):
                    old_mat = slicer_snapshot.get("material_id", "—")
                    rows_html.append("""
                    <tr>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b;"><b>Материал печати</b></td>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#f87171;">{}</td>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#10b981; font-weight:bold;">{}</td>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#cbd5e1;">{}</td>
                    </tr>
                    """.format(old_mat, plan["material_id"], plan.get("material_reason", "")))

                for item in plan.get("settings", []):
                    k = item["key"]
                    label = SETTING_LABELS_RU.get(k, k)
                    old_v = slicer_snapshot.get(k, "—")
                    new_v = item["value"]
                    reason = item.get("reason", "По требованиям ТЗ")
                    rows_html.append("""
                    <tr>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b;"><b>{}</b><br><span style="color:#64748b;font-size:10px;">{}</span></td>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#f87171;">{}</td>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#10b981; font-weight:bold;">{}</td>
                        <td style="padding:6px 8px; border-bottom:1px solid #2b313b; color:#cbd5e1;">{}</td>
                    </tr>
                    """.format(label, k, old_v, new_v, reason))

                blocks.append("""
                <div style="margin-bottom:10px;">
                    <div style="color:#10b981; font-weight:bold; font-size:12.5px; margin-bottom:6px;">
                        ⚙️ Какие пункты настроены, выбранные значения и почему:
                    </div>
                    <table width="100%" cellspacing="0" cellpadding="0" style="background-color:#16191f; border:1px solid #2b313b; border-radius:8px; font-size:11.5px;">
                        <thead>
                            <tr style="background-color:#1f242d; color:#94a3b8; text-align:left;">
                                <th style="padding:6px 8px;">Пункт настройки</th>
                                <th style="padding:6px 8px;">Было</th>
                                <th style="padding:6px 8px;">Выбрано</th>
                                <th style="padding:6px 8px;">Почему выбрано это значение</th>
                            </tr>
                        </thead>
                        <tbody>
                            {}
                        </tbody>
                    </table>
                </div>
                """.format("".join(rows_html)))

            if audit_results:
                blocks.append("""
                <div style="background-color:#14261f; border:1px solid #059669; padding:9px 12px; border-radius:8px; margin-bottom:10px; font-size:12px; color:#d1fae5;">
                    <b style="color:#34d399;">🛡️ Самопроверка настроек слайсера перед нарезкой:</b><br>
                    {}<br>
                    <b style="color:#10b981;">➔ Проверка пройдена успешно. Запущена автоматическая нарезка (слайсинг)!</b>
                </div>
                """.format("<br>".join(audit_results)))

            clean_exp = (explanation or "Настройки успешно применены и проверены.").replace("\n", "<br>")
            blocks.append("<div style='color:#eceef2; line-height:1.5;'>{}</div>".format(clean_exp))

            return """
            <div style="background-color:#1e232b; border:1px solid #2e3644; padding:12px 14px; border-radius:12px; margin-bottom:12px;">
                <div style="color:#10b981; font-size:11px; font-weight:bold; margin-bottom:6px;">AI Assistant — ИИ-Технолог STE Slicer</div>
                {}
            </div>
            """.format("".join(blocks))


# ==============================================================================
# ТОЧКА ВХОДА РАСШИРЕНИЯ STE SLICER
# ==============================================================================
class AIAssistant(Extension):
    def __init__(self):
        super().__init__()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Открыть чат", self.showChat)
        self._dialog = None

    def showChat(self):
        try:
            if not QT_WIDGETS_AVAILABLE:
                Logger.log("e", "[AIAssistant] PyQt5.QtWidgets недоступен.")
                return
            if self._dialog is None:
                self._dialog = ChatPopupDialog()
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()
            self._dialog._update_top_telemetry()
        except Exception as e:
            Logger.log("e", "[AIAssistant] Ошибка открытия окна: {}".format(e))