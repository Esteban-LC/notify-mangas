#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Limpia valores inválidos de last_chapter en manga_library.yml
- Elimina años (1900–2100) y números enormes (>3000)
- Mantiene capítulos plausibles (0 < n <= 3000)
- Hace backup manga_library.yml.bak

Uso:
  python fix_yaml.py           # limpia en caliente
  python fix_yaml.py --dry     # solo muestra lo que haría
"""

from __future__ import annotations
import sys
import shutil
import math
import yaml

LIB_PATH = "manga_library.yml"
BACKUP_PATH = LIB_PATH + ".bak"

def plausible_chapter(x) -> bool:
    """Regla simple: evitar años e IDs enormes."""
    try:
        v = float(x)
    except Exception:
        return False
    # filtrar NaN/Inf
    if math.isnan(v) or math.isinf(v):
        return False
    # descartar años evidentes
    if 1900 <= v <= 2100:
        return False
    # descartar IDs gigantes
    if v > 3000:
        return False
    # debe ser positivo
    if v <= 0:
        return False
    return True

def main() -> int:
    dry = "--dry" in sys.argv or "--dry-run" in sys.argv

    try:
        with open(LIB_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"[ERR] No existe {LIB_PATH}")
        return 2

    series = data.get("series", [])
    if not isinstance(series, list):
        print("[ERR] Formato inesperado de YAML (no hay 'series' lista).")
        return 3

    changed = False
    touched = []

    for s in series:
        name = (s.get("name") or "").strip() or "<sin nombre>"
        lc = s.get("last_chapter", None)

        if lc is None or lc == "":
            continue

        if plausible_chapter(lc):
            # normaliza a float con 1 decimal (si aplica)
            try:
                v = float(lc)
            except Exception:
                s["last_chapter"] = None
                changed = True
                touched.append((name, lc, None, "no-numérico"))
                continue

            # redondea a 1 decimal si tiene muchísimas decimales
            v2 = round(v, 1) if abs(v - round(v)) > 1e-9 else int(round(v))
            if v2 != lc:
                s["last_chapter"] = v2
                changed = True
                touched.append((name, lc, v2, "normalizado"))
        else:
            s["last_chapter"] = None
            changed = True
            touched.append((name, lc, None, "inválido"))

    if not changed:
        print("Nada que limpiar :)")
        return 0

    # backup
    if not dry:
        shutil.copyfile(LIB_PATH, BACKUP_PATH)

    # guarda
    if not dry:
        with open(LIB_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    print("Cambios:")
    for name, old, new, reason in touched:
        if new is None:
            print(f" - {name}: {old} -> None  ({reason})")
        else:
            print(f" - {name}: {old} -> {new}   ({reason})")

    if not dry:
        print(f"\nListo. Backup en: {BACKUP_PATH}")
    else:
        print("\n(Ejecución en seco; no se modificó el archivo)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
