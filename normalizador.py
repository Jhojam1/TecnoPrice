"""
TecnoPrice CO - Normalizador de productos
------------------------------------------
Genera una clave de agrupación estable (grupo_clave) a partir del nombre
comercial y la categoría. Esta clave se usa para identificar el mismo
producto ofrecido por distintas tiendas.

Reglas básicas (heurísticas):
- RAM:  Categoria=RAM | DDR{N} | {CAPACIDAD}GB | {VELOCIDAD} | {MARCA} | {LINEA}
- SSD:  Categoria=SSD | {CAPACIDAD}{TB/GB} | {FORMFACTOR} | {GEN} | {MARCA} | {LINEA}
- GPU:  Categoria=GPU | {FAMILIA}-{MODELO} | {VRAM}GB | {MARCA} | {LINEA}

Si no se encuentra suficiente información, se devuelve una clave parcial
pero estable a partir de tokens del nombre.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional


BRANDS = [
    "ADATA", "AORUS", "ASROCK", "ASUS", "CRUCIAL", "CORSAIR", "EVGA", "GALAX",
    "GIGABYTE", "HP", "HIKVISION", "HYPERX", "INTEL", "KINGSTON", "KINGTON",
    "KIOXIA", "MICRON", "MSI", "PNY", "PATRIOT", "SAMSUNG", "SEAGATE",
    "SILICON POWER", "TEAMGROUP", "TEAM", "WESTERN DIGITAL", "WD", "XPG",
]

GPU_FAMILIES = ["RTX", "GTX", "RX"]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _clean(s: str) -> str:
    s = _strip_accents(s).upper()
    s = re.sub(r"[^A-Z0-9\.\-\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _find_brand(text: str) -> Optional[str]:
    for b in BRANDS:
        if b in text:
            return b
    return None


def _first(*vals: Optional[str]) -> Optional[str]:
    for v in vals:
        if v:
            return v
    return None


def _norm_line(text: str, brand: Optional[str]) -> Optional[str]:
    if not brand:
        return None
    # Toma una "línea" o palabra destacada distinta a la marca y a tokens genéricos
    tokens = [t for t in text.split() if len(t) >= 3 and t not in {brand, "MEMORIA", "RAM", "SSD", "DISCO", "SOLIDO", "NVME", "PCIe", "PCIE"}]
    # Elige una token que parezca una línea de producto (ej: VENGEANCE, PRO, NV2, SN770, VENTUS)
    for t in tokens:
        if re.match(r"^[A-Z0-9]{3,}$", t) and not re.match(r"^DDR\d$", t):
            return t
    return None


def _grupo_ram(text: str, brand: Optional[str]) -> Optional[str]:
    ddr = None
    m = re.search(r"DDR\s*-?\s*([3-5])\b", text)
    if m:
        ddr = f"DDR{m.group(1)}"

    cap = None
    m = re.search(r"(\d{1,3})\s*(GB|G)\b", text)
    if m:
        cap = f"{m.group(1)}GB"

    speed = None
    m = re.search(r"(\d{3,5})\s*(MHZ|MT/?S|)\b", text)
    if m:
        speed = m.group(1)

    line = _norm_line(text, brand)

    parts = ["RAM", ddr, cap, speed, brand, line]
    parts = [p for p in parts if p]
    return "|".join(parts) if parts else None


def _grupo_ssd(text: str, brand: Optional[str]) -> Optional[str]:
    cap = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(TB|GB)\b", text)
    if m:
        num = m.group(1).rstrip(".0")
        cap = f"{num}{m.group(2)}"

    formf = None
    if re.search(r"M\.?2\b", text):
        formf = "M2"
    elif "SATA" in text:
        formf = "SATA"

    gen = None
    m = re.search(r"GEN\s*-?\s*(3|4|5)\b", text)
    if m:
        gen = f"GEN{m.group(1)}"
    elif re.search(r"PCI\s*E\s*GEN\s*(3|4|5)", text):
        g = re.search(r"(3|4|5)", text)
        if g:
            gen = f"GEN{g.group(1)}"

    line = _norm_line(text, brand) or ("NVME" if "NVME" in text else None)

    parts = ["SSD", cap, formf, gen, brand, line]
    parts = [p for p in parts if p]
    return "|".join(parts) if parts else None


def _grupo_gpu(text: str, brand: Optional[str]) -> Optional[str]:
    fam = None
    for f in GPU_FAMILIES:
        if f in text:
            fam = f
            break
    model = None
    if fam:
        m = re.search(rf"{fam}\s*-?\s*(\d{{3,5}})", text)
        if m:
            model = m.group(1)
    vram = None
    m = re.search(r"(\d{1,3})\s*GB\b", text)
    if m:
        vram = f"{m.group(1)}GB"

    line = _norm_line(text, brand)

    fam_model = f"{fam}-{model}" if fam and model else None
    parts = ["GPU", fam_model, vram, brand, line]
    parts = [p for p in parts if p]
    return "|".join(parts) if parts else None


def normalizar_producto(nombre: str, categoria: str) -> Dict[str, Optional[str]]:
    """
    Devuelve un dict con al menos la clave `grupo_clave` para agrupar productos.
    Puede incluir otras claves en el futuro.
    """
    nombre_c = _clean(nombre)
    categoria_c = _clean(categoria)
    brand = _find_brand(nombre_c)

    grupo = None
    if "RAM" in categoria_c or re.search(r"\bDDR[3-5]\b", nombre_c):
        grupo = _grupo_ram(nombre_c, brand)
    elif any(k in categoria_c for k in ["SSD", "SOLIDO", "DISCO"]) or ("NVME" in nombre_c or "M2" in nombre_c or "M.2" in nombre_c):
        grupo = _grupo_ssd(nombre_c, brand)
    elif any(k in categoria_c for k in ["GPU", "VIDEO", "GRAFICA", "GRAFICO", "TARJETA"]) or any(f in nombre_c for f in GPU_FAMILIES):
        grupo = _grupo_gpu(nombre_c, brand)

    if not grupo:
        # Fallback genérico: seleccionar algunos tokens estables
        tokens = [t for t in nombre_c.split() if len(t) >= 3][:6]
        base = categoria_c.split()[0] if categoria_c else "PROD"
        grupo = "|".join([base] + tokens)

    return {"grupo_clave": grupo, "marca": brand}
