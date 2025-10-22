#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import time
import math
import argparse
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import yaml
from bs4 import BeautifulSoup

LIB_PATH = "manga_library.yml"

# --------- Sesión HTTP "humana" + Proxy opcional ----------
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.6613.84 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://google.com/",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
})
PROXY_URL = os.getenv("PROXY_URL", "").strip()
if PROXY_URL:
    SESSION.proxies = {"http": PROXY_URL, "https": PROXY_URL}

REQUEST_TIMEOUT = 25
MAX_RETRIES = 2
RETRY_SLEEP = 2.5

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


# ------------------ Utilidades ----------------------------

def get(url: str) -> requests.Response:
    """
    GET con reintentos básicos.
    """
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_SLEEP * attempt)
            else:
                raise
    raise last_exc  # pragma: no cover


def to_float_chapter(text: str) -> Optional[float]:
    """
    Extrae el primer número tipo capítulo (puede tener decimal).
    """
    # Soporta "Capítulo 135.50" o "Capítulo 136", etc.
    m = re.search(r'cap[ií]tulo\s+(\d+(?:\.\d+)?)', text, re.I)
    if not m:
        # fallback: buscar cualquier número (caso extremo)
        m = re.search(r'(\d+(?:\.\d+)?)', text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def send_discord(content: str):
    if not DISCORD_WEBHOOK:
        print(f"[INFO] Discord no configurado, mensaje:\n{content}\n")
        return
    try:
        r = SESSION.post(DISCORD_WEBHOOK, json={"content": content}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[WARN] Falló envío a Discord: {e}")


# ----------------- Parsers por dominio --------------------

def parse_manga_oni(html: str) -> Optional[float]:
    """
    manga-oni.com serie: revisar #c_list h3 con 'Capítulo'
    """
    soup = BeautifulSoup(html, "lxml")
    caps = []
    for h3 in soup.select("#c_list h3"):
        t = h3.get_text(" ", strip=True)
        ch = to_float_chapter(t)
        if ch is not None:
            caps.append(ch)
    if caps:
        return max(caps)
    return None


def parse_mangasnosekai(html: str) -> Optional[float]:
    """
    mangasnosekai.com: lista de 'Capítulo N' en el grid de capítulos.
    """
    soup = BeautifulSoup(html, "lxml")
    caps = []
    # varios selectores posibles:
    for node in soup.select(".container-capitulos .contenedor-capitulo-miniatura, .grid-capitulos .contenedor-capitulo-miniatura"):
        text = node.get_text(" ", strip=True)
        ch = to_float_chapter(text)
        if ch is not None:
            caps.append(ch)
    if not caps:
        # fallback: buscar en toda la página
        text = soup.get_text(" ", strip=True)
        for m in re.finditer(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', text, re.I):
            try:
                caps.append(float(m.group(1)))
            except ValueError:
                pass
    if caps:
        return max(caps)
    return None


def parse_zonatmo(html: str) -> Optional[float]:
    """
    zonatmo.com: 'Capítulo X.YY ...' aparece en los headers h4/a de la lista.
    """
    soup = BeautifulSoup(html, "lxml")
    caps = []
    # Header de cada capítulo (colapsable)
    for a in soup.select(".chapters li.upload-link h4 a.btn-collapse"):
        ch = to_float_chapter(a.get_text(" ", strip=True))
        if ch is not None:
            caps.append(ch)
    if not caps:
        # fallback: buscar en toda la página
        text = soup.get_text(" ", strip=True)
        for m in re.finditer(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', text, re.I):
            try:
                caps.append(float(m.group(1)))
            except ValueError:
                pass
    if caps:
        return max(caps)
    return None


def parse_m440(html: str) -> Optional[float]:
    """
    m440.in: buscar 'Capítulo N' globalmente (estructura varía).
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    caps = []
    for m in re.finditer(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', text, re.I):
        try:
            caps.append(float(m.group(1)))
        except ValueError:
            pass
    if caps:
        return max(caps)
    return None


PARSERS = {
    "manga-oni.com": parse_manga_oni,
    "www.manga-oni.com": parse_manga_oni,

    "mangasnosekai.com": parse_mangasnosekai,
    "www.mangasnosekai.com": parse_mangasnosekai,

    "zonatmo.com": parse_zonatmo,
    "www.zonatmo.com": parse_zonatmo,

    "m440.in": parse_m440,
    "www.m440.in": parse_m440,
}


# ----------------- Lógica principal -----------------------

def load_library(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        print(f"[ERROR] No encuentro {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    # normalizar entradas antiguas (si vinieran con otras claves)
    norm = []
    for it in data:
        if not isinstance(it, dict):
            continue
        name = it.get("name") or it.get("title") or it.get("manga") or "Sin nombre"
        url = it.get("url") or it.get("link") or ""
        last = it.get("last_chapter")
        norm.append({"name": name, "url": url, "last_chapter": last})
    return norm


def write_library(path: str, items: List[Dict[str, Any]]):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(items, f, sort_keys=False, allow_unicode=True)


def get_latest_chapter(series_url: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Devuelve (capítulo, error_text). Si hay error, capítulo=None y error_text con mensaje.
    """
    dom = domain(series_url)
    parser = PARSERS.get(dom)
    if not parser:
        return None, f"Sin parser para dominio: {dom}"
    try:
        resp = get(series_url)
        ch = parser(resp.text)
        if ch is None:
            return None, "No pude detectar capítulo en el HTML"
        return ch, None
    except requests.HTTPError as e:
        return None, f"{e.response.status_code} Client Error: {e}"
    except Exception as e:
        return None, f"Error: {e}"


def fmt_ch(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if math.isclose(v, int(v)):
        return str(int(v))
    return f"{v:.2f}"


def run(check_only: bool = False) -> int:
    lib = load_library(LIB_PATH)
    any_change = False

    updated_msgs = []
    error_msgs = []

    for item in lib:
        name = item.get("name")
        url = item.get("url")
        prev = item.get("last_chapter")

        if not url:
            error_msgs.append(f"**{name}**: URL vacía.")
            continue

        latest, err = get_latest_chapter(url)
        if err:
            error_msgs.append(f"**{name}**: {err} — <{url}>")
            continue

        # Si last_chapter no estaba, lo inicializamos
        if prev is None:
            item["last_chapter"] = latest
            any_change = True
            print(f"[BOOTSTRAP] {name}: set last_chapter={fmt_ch(latest)}")
            continue

        if latest is not None and (prev is None or latest > float(prev)):
            # Actualización
            item["last_chapter"] = latest
            any_change = True
            updated_msgs.append(
                f"📗 **{name}** — Nuevo capítulo: **{fmt_ch(latest)}** (antes {fmt_ch(prev)})\n<{url}>"
            )
            print(f"[UPDATE] {name}: {fmt_ch(prev)} -> {fmt_ch(latest)}")
        else:
            print(f"[OK] {name}: sin cambios (último {fmt_ch(prev)})")

    # guardar cambios
    if any_change and not check_only:
        write_library(LIB_PATH, lib)

    # Notificaciones
    if updated_msgs:
        send_discord("**Novedades de manga**:\n\n" + "\n\n".join(updated_msgs))
    else:
        print("Sin novedades.")
        # opcional: enviar también a Discord
        # send_discord("Sin novedades.")

    if error_msgs:
        send_discord("⚠️ **Errores**:\n\n" + "\n".join(error_msgs))
        # No hacemos fail del job por errores 403 / parciales.

    return 0


def main():
    p = argparse.ArgumentParser(description="Checker unificado de mangas")
    p.add_argument("--check-only", action="store_true",
                   help="No escribas en el YAML (solo comparar / notificar).")
    args = p.parse_args()
    sys.exit(run(check_only=args.check_only))


if __name__ == "__main__":
    main()
