#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import math
import html
import random
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

import yaml
import requests
from bs4 import BeautifulSoup

# -----------------------------------
# Configuración básica
# -----------------------------------

LIB_FILE = "manga_library.yml"
STATUS_FILE = "status.json"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "").strip()
EXTRA_COOKIES_JSON = os.getenv("EXTRA_COOKIES_JSON", "").strip()

USE_PROXY = bool(PROXY_URL)
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if USE_PROXY else None

# -----------------------------------
# Utilidades: status.json (mensaje fijo “sin novedades”)
# -----------------------------------

def _load_status() -> Dict[str, Any]:
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_status(data: Dict[str, Any]) -> None:
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _discord_upsert_status(webhook_url: str, content: str) -> None:
    """
    Crea o edita (si existe) un mensaje 'status' del webhook para no spamear.
    Guarda el message_id en status.json para poder editarlo después.
    """
    if not webhook_url:
        return  # sin webhook

    status = _load_status()
    msg_id = status.get("status_message_id")

    payload = {
        "content": content,
        "allowed_mentions": {"parse": []}
    }

    try:
        if msg_id:
            # Editar el mensaje existente
            edit_url = f"{webhook_url}/messages/{msg_id}"
            r = requests.patch(edit_url, json=payload, timeout=20)
            if r.status_code == 404:
                # No existe (lo borraron), creamos uno nuevo
                r = requests.post(webhook_url, json=payload, timeout=20)
                r.raise_for_status()
                data = r.json()
                status["status_message_id"] = data.get("id")
                _save_status(status)
            else:
                r.raise_for_status()
        else:
            # Crear primer mensaje de estado
            r = requests.post(webhook_url, json=payload, timeout=20)
            r.raise_for_status()
            data = r.json()
            status["status_message_id"] = data.get("id")
            _save_status(status)

        # Guardar timestamp de última revisión
        status["last_check_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        _save_status(status)

    except requests.RequestException as e:
        print(f"[WARN] No pude actualizar el mensaje de estado en Discord: {e}")

# -----------------------------------
# Carga/guardado de librería
# -----------------------------------

def load_library() -> Dict[str, Any]:
    if not os.path.exists(LIB_FILE):
        return {"series": []}
    with open(LIB_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "series" not in data or not isinstance(data["series"], list):
        data["series"] = []
    return data

def save_library(lib: Dict[str, Any]) -> None:
    with open(LIB_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(lib, f, allow_unicode=True, sort_keys=False)

# -----------------------------------
# HTTP helpers
# -----------------------------------

def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    # Cookies extra (si pasas JSON en secreto)
    if EXTRA_COOKIES_JSON:
        try:
            extra_cookies = json.loads(EXTRA_COOKIES_JSON)
            if isinstance(extra_cookies, dict):
                for k, v in extra_cookies.items():
                    s.cookies.set(k, v)
        except Exception as e:
            print(f"[WARN] No pude parsear EXTRA_COOKIES_JSON: {e}")
    return s

def fetch_html(url: str, session: requests.Session, retries: int = 2, sleep_s: float = 1.0) -> Optional[str]:
    last_e = None
    for _ in range(retries + 1):
        try:
            r = session.get(url, proxies=PROXIES, timeout=25)
            if r.status_code == 403:
                # a veces user-agent/cookies/proxy ayudan; aquí devolvemos igual para tratar parseo gracioso
                print(f"[WARN] 403 Forbidden en {url}")
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_e = e
            time.sleep(sleep_s + random.random())
    print(f"[ERROR] No se pudo obtener {url}: {last_e}")
    return None

# -----------------------------------
# Parsers por sitio (devuelven último capítulo como float o int, y título opcional)
# -----------------------------------

_chapter_num_re = re.compile(r"cap[ií]tulo\s*([0-9]+(?:\.[0-9]+)?)", re.I)

def _extract_first_chapter_number(text: str) -> Optional[float]:
    m = _chapter_num_re.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None

def parse_mangasnosekai(html_text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Busca el primer 'Capítulo X' en la lista de capítulos.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    # Los items suelen tener "Capítulo NN" en tarjetas/grid
    last = None
    for tag in soup.find_all(text=_chapter_num_re):
        num = _extract_first_chapter_number(tag)
        if num is not None:
            if last is None or num > last:
                last = num
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else None
    return last, title

def parse_m440(html_text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    m440.in — similar: buscar 'Capítulo NN' en la página del manga
    """
    soup = BeautifulSoup(html_text, "html.parser")
    last = None
    for tag in soup.find_all(text=_chapter_num_re):
        num = _extract_first_chapter_number(tag)
        if num is not None:
            if last is None or num > last:
                last = num
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else None
    return last, title

def parse_zonatmo(html_text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    ZonaTMO — en la ficha del manhwa/manhua suele listar capítulos tipo 'Capítulo 1.00', etc.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    last = None
    for tag in soup.find_all(text=_chapter_num_re):
        num = _extract_first_chapter_number(tag)
        if num is not None:
            if last is None or num > last:
                last = num
    # Título
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    return last, title

def parse_animebbg(html_text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Tu XenForo (animebbg) — en /capitulos aparecen 'Capítulo X.XX'
    """
    soup = BeautifulSoup(html_text, "html.parser")
    last = None
    # Buscar tarjetas que contengan 'Capítulo NN'
    for tag in soup.find_all(text=_chapter_num_re):
        num = _extract_first_chapter_number(tag)
        if num is not None:
            if last is None or num > last:
                last = num
    # Título: suele estar arriba en algún h1/h2
    title = None
    h_el = soup.find(["h1", "h2"])
    if h_el:
        title = h_el.get_text(" ", strip=True)
    return last, title

# -----------------------------------
# Envío a Discord
# -----------------------------------

def send_discord_new(webhook_url: str, title: str, chapter_num: float, url: str, image_url: Optional[str] = None):
    if not webhook_url:
        return
    ch_pretty = f"{chapter_num:.2f}".rstrip("0").rstrip(".")
    embed = {
        "title": f"{title}",
        "description": f"**Nuevo capítulo detectado:** Capítulo {ch_pretty}",
        "url": url,
        "color": 0x00B894,  # verde
        "footer": {"text": "Actualización de capítulos"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    if image_url:
        embed["thumbnail"] = {"url": image_url}

    payload = {
        "content": None,
        "embeds": [embed],
        "allowed_mentions": {"parse": []}
    }
    try:
        r = requests.post(webhook_url, json=payload, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] No pude notificar a Discord: {e}")

# -----------------------------------
# Router de sites
# -----------------------------------

def detect_site_parser(url: str):
    url_l = url.lower()
    if "mangasnosekai.com" in url_l:
        return parse_mangasnosekai
    if "m440.in" in url_l:
        return parse_m440
    if "zonatmo.com" in url_l:
        return parse_zonatmo
    if "animebbg.net" in url_l:
        return parse_animebbg
    return None

# -----------------------------------
# Main
# -----------------------------------

def main():
    session = build_session()
    lib = load_library()
    series_list: List[Dict[str, Any]] = lib.get("series", [])

    updates = []
    checked = 0

    for item in series_list:
        name = (item.get("name") or "").strip()
        url = (item.get("url") or "").strip()
        last_chapter_saved = item.get("last_chapter", 0)

        if not url:
            continue

        parser = detect_site_parser(url)
        if not parser:
            print(f"[WARN] No tengo parser para: {url} — «{name}»")
            continue

        checked += 1
        html_text = fetch_html(url, session)
        if not html_text:
            print(f"[ERROR] No pude leer HTML: {url}")
            continue

        try:
            last_chapter_found, title_found = parser(html_text)
        except Exception as e:
            print(f"[ERROR] Falló el parser para {url}: {e}")
            continue

        if last_chapter_found is None:
            print(f"[WARN] No detecté número de capítulo en: {url}")
            continue

        title_use = title_found or name or "Nuevo capítulo"
        # Si hay un capítulo nuevo
        if float(last_chapter_found) > float(last_chapter_saved or 0):
            # Notificar
            send_discord_new(
                DISCORD_WEBHOOK_URL,
                title_use,
                float(last_chapter_found),
                url,
                image_url=None
            )
            # Guardar update
            updates.append(
                (title_use, float(last_chapter_found), url)
            )
            # Actualizar en memoria
            item["last_chapter"] = float(last_chapter_found)

        # Evitar dar demasiada caña a los sitios
        time.sleep(random.uniform(0.6, 1.2))

    # Guardar librería si hubo cambios
    save_library(lib)

    # --- Mensaje de ESTADO en Discord (se edita siempre el mismo) ---
    series_count = len(series_list)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if updates:
        status_text = f"✅ Novedades detectadas — última revisión: {now_utc} · ({series_count} series)"
        _discord_upsert_status(DISCORD_WEBHOOK_URL, status_text)
    else:
        status_text = f"🕒 Sin novedades — última revisión: {now_utc} · ({series_count} series)"
        _discord_upsert_status(DISCORD_WEBHOOK_URL, status_text)

    # Log de bootstrap para Actions
    if updates:
        for t, ch, u in updates:
            print(f"[UPDATE] {t} -> {ch} | {u}")
    else:
        print("Sin novedades.")


if __name__ == "__main__":
    main()
