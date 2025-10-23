#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import math
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
import yaml

# -----------------------------
# Configuración básica
# -----------------------------
LIB_PATH = os.getenv("LIB_PATH", "manga_library.yml")
USER_AGENT = os.getenv(
    "SCRAPER_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
TIMEOUT = 25

# PROXY opcional: http://user:pass@host:port  o  http://host:port
PROXY_URL = os.getenv("PROXY_URL", "").strip()
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# Cookies extra opcionales (JSON)
EXTRA_COOKIES_JSON = os.getenv("EXTRA_COOKIES_JSON", "").strip()
EXTRA_COOKIES = {}
if EXTRA_COOKIES_JSON:
    try:
        EXTRA_COOKIES = json.loads(EXTRA_COOKIES_JSON)
    except Exception:
        EXTRA_COOKIES = {}

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
}

# -----------------------------
# Utilidades YAML
# -----------------------------
def load_series(path: str = LIB_PATH) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    series = data.get("series", [])
    # normaliza tipos
    for it in series:
        it["name"] = str(it.get("name", "")).strip()
        it["site"] = str(it.get("site", "")).strip()
        it["url"] = str(it.get("url", "")).strip()
        # last_chapter puede venir como str -> convierte a float si aplica
        lc = it.get("last_chapter", None)
        if lc is None or lc == "":
            it["last_chapter"] = None
        else:
            try:
                it["last_chapter"] = float(str(lc).replace(",", "."))
            except Exception:
                it["last_chapter"] = None
    return series

def save_series(items: List[Dict], path: str = LIB_PATH) -> None:
    data = {"series": items}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

# -----------------------------
# Descarga HTML con tolerancia
# -----------------------------
def fetch(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Devuelve (html, err). Si hay error, html=None y err=mensaje.
    """
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        if EXTRA_COOKIES:
            s.cookies.update(EXTRA_COOKIES)

        resp = s.get(url, timeout=TIMEOUT, proxies=PROXIES, allow_redirects=True)
        if resp.status_code >= 400:
            return None, f"{resp.status_code} {resp.reason} en {url}"
        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct and "application/json" not in ct:
            # alguna páginas devuelven json de capítulos: igual lo tratamos como texto
            return resp.text, None
        return resp.text, None
    except Exception as e:
        return None, str(e)

# -----------------------------
# Regex de números
# -----------------------------
# Focalizamos primero en marcadores fuertes (atributos/data y href de capítulo)
_NUMBER_PATTERNS = [
    re.compile(r'data-number="(\d+(?:[_\.]\d+)?)"', re.I),                   # data-number="166_5"
    re.compile(r'/(\d+(?:_\d+)?)\-[a-z0-9]+["\']', re.I),                    # /166_5-abc"
    # luego medios
    re.compile(r'#\s*(\d+(?:\.\d+)?)\s*[<\s⠇]', re.I),                       # #166 < ó #166⠇
    re.compile(r'cap[ií]tulo\s*(\d+(?:\.\d+)?)', re.I),                      # Capítulo 166.5
    # por último JSON incrustado
    re.compile(r'"number"\s*:\s*"(\d+(?:\.\d+)?)"', re.I),
]

def extract_latest_from_html(html: str) -> Optional[float]:
    """
    Devuelve el último capítulo real encontrado, filtrando años/fechas y outliers.
    Reglas:
      - Descarta números entre 1900 y 2100 (años/fechas).
      - Descarta >= 10000 (ruido).
      - Acepta 159.5 y 166_5 -> 166.5.
    Prioridad: patrones fuertes -> medios -> json.
    """

    def to_float(raw: str) -> Optional[float]:
        try:
            return float(raw.replace("_", "."))
        except Exception:
            return None

    def clean(nums: List[float]) -> List[float]:
        out = []
        for x in nums:
            if 1900 <= x <= 2100:    # años/fechas
                continue
            if x >= 10000:           # ruido brutal (p.ej. 20381.0)
                continue
            out.append(x)
        return out

    # 1) patrones fuertes
    strong: List[float] = []
    for pat in _NUMBER_PATTERNS[:2]:
        for m in pat.findall(html):
            f = to_float(m if isinstance(m, str) else m[0])
            if f is not None:
                strong.append(f)
    strong = clean(strong)
    if strong:
        return max(strong)

    # 2) patrones medios
    mid: List[float] = []
    for pat in _NUMBER_PATTERNS[2:4]:
        for m in pat.findall(html):
            f = to_float(m if isinstance(m, str) else m[0])
            if f is not None:
                mid.append(f)
    mid = clean(mid)
    if mid:
        return max(mid)

    # 3) JSON incrustado
    soft: List[float] = []
    for m in _NUMBER_PATTERNS[4].findall(html):
        f = to_float(m if isinstance(m, str) else m[0])
        if f is not None:
            soft.append(f)
    soft = clean(soft)
    if soft:
        return max(soft)

    return None

# -----------------------------
# Notificación a Discord
# -----------------------------
def notify_discord_blocking(lines: List[str]) -> None:
    """Manda a Discord en bloques de 1900 caracteres."""
    webhook = os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK") or ""
    if not webhook:
        print("[WARN] DISCORD_WEBHOOK no configurado; no se enviará notificación.")
        return

    from notify_discord import send_lines
    try:
        send_lines(webhook, lines)
    except Exception as e:
        print(f"[WARN] Error notificando a Discord: {e}")

# -----------------------------
# Core
# -----------------------------
@dataclass
class Result:
    item: Dict
    latest: Optional[float]
    error: Optional[str]

def check_item(item: Dict) -> Result:
    url = item["url"]
    html, err = fetch(url)
    if err:
        return Result(item, None, f"No se pudo obtener {url}: {err}")
    latest = extract_latest_from_html(html or "")
    return Result(item, latest, None if latest is not None else f"No pude encontrar capítulo en: {url}")

def main() -> None:
    items = load_series()
    changes: List[str] = []
    warns: List[str] = []

    updated = False

    for it in items:
        try:
            r = check_item(it)
            if r.error:
                warns.append(f"• {r.error} — «{it['name']}»")
                # si no hay latest, no tocamos last_chapter
                print(f"[WARN] {r.error}")
                continue

            old = it.get("last_chapter")
            new = r.latest

            if old is None and new is not None:
                it["last_chapter"] = new
                updated = True
                changes.append(f"[NUEVO] {it['name']} — 0.0 -> {new}")
                print(f"[NUEVO] {it['name']} — 0.0 -> {new}")
            elif new is not None and (old is None or new > float(old)):
                it["last_chapter"] = new
                updated = True
                changes.append(f"[NUEVO] {it['name']} — {old} -> {new}")
                print(f"[NUEVO] {it['name']} — {old} -> {new}")
            else:
                print(f"[OK] Sin cambios: {it['name']} (último {old})")

        except Exception as e:
            msg = f"Error al parsear {it['url']}: {e}"
            warns.append(f"• {msg}")
            print(f"[WARN] {msg}")
            traceback.print_exc()

    # Guarda si hubo cambios
    if updated:
        save_series(items)

    # Arma mensaje para Discord
    if changes or warns:
        lines: List[str] = []
        if not changes:
            lines.append("Sin novedades.")
        else:
            lines.append("Novedades:")
            lines.extend([f"• {x}" for x in changes])
        if warns:
            lines.append("")
            lines.append("Avisos/errores:")
            lines.extend(warns)
        notify_discord_blocking(lines)
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    main()
