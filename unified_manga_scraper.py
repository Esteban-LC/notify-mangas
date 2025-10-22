#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import time
import math
import json
import yaml
import random
import urllib.parse as urlparse
from typing import Optional, Tuple
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

# ------------------------------
# Config
# ------------------------------

UA_LIST = [
    # Un puñado de UA reales para rotar
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.69 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; rv:122.0) Gecko/20100101 Firefox/122.0",
]

DEFAULT_HEADERS = {
    "User-Agent": random.choice(UA_LIST),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

TIMEOUT = 25
RETRIES = 3
BACKOFF = 2.0

CHAPTER_WORDS = [
    "capítulo", "capitulo", "chapter", "cap", "ch", "episodio", "episode"
]

# ------------------------------
# Helpers
# ------------------------------

def log_warn(msg: str):
    print(f"[WARN] {msg}")

def normalize_number(txt: str) -> Optional[float]:
    """
    Convierte strings tipo:
      '54.1' -> 54.1
      '49-1' -> 49.1
      '7-20' -> 7.20
      '150' -> 150.0
    Devuelve float o None.
    """
    txt = txt.strip()
    # #166.5  → 166.5
    m = re.search(r"(\d+(?:\.\d+)?)", txt)
    if m and m.group(1):
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # 49-1 → 49.1 / 7-20 → 7.20
    m = re.search(r"(\d+)-(\d+)", txt)
    if m:
        left, right = m.group(1), m.group(2)
        try:
            return float(f"{left}.{right}")
        except ValueError:
            return None

    return None

def parse_chapter_from_text(text: str) -> Optional[float]:
    """
    Busca patrones comunes de capítulo en un texto.
    """
    # Ej: "Capítulo 54.1", "Capitulo 49-1", "Ch. 21.2", "#168", etc.
    patterns = [
        r"#\s*(\d+(?:\.\d+)?(?:-\d+)?)",
        r"(?:cap[ií]tulo|cap|ch(?:apter)?)\s*[:#]?\s*(\d+(?:\.\d+)?(?:-\d+)?)",
        r"(?:episodio|episode)\s*[:#]?\s*(\d+(?:\.\d+)?(?:-\d+)?)",
    ]
    low = text.lower()
    for pat in patterns:
        m = re.search(pat, low, flags=re.IGNORECASE)
        if m:
            return normalize_number(m.group(1))
    return None

def fetch_html(url: str) -> str:
    """
    Descarga HTML con reintentos y headers “reales”.
    """
    last_err = None
    session = requests.Session()
    # cookies ayudan a “parecer navegador”
    session.headers.update(DEFAULT_HEADERS)
    session.headers["User-Agent"] = random.choice(UA_LIST)

    for i in range(RETRIES):
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            if resp.status_code == 403:
                # Cambia UA y reintenta
                session.headers["User-Agent"] = random.choice(UA_LIST)
                last_err = Exception(f"403 Forbidden en {url}")
                time.sleep(BACKOFF * (i + 1))
                continue
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_err = e
            time.sleep(BACKOFF * (i + 1))
    raise last_err

def domain_of(url: str) -> str:
    return urlparse.urlparse(url).netloc.lower().strip()

# ------------------------------
# Parsers por sitio
# ------------------------------

def parse_madara_latest(html: str) -> Optional[float]:
    """
    Parser genérico para temas Madara (bokugents.com, mangasnosekai.com).
    Busca la lista de capítulos estándar.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) Lista estándar
    container = soup.select_one(".listing-chapters_wrap ul.main") \
        or soup.select_one(".page-content-listing .listing-chapters_wrap ul.main")

    if container:
        # El primero suele ser el más nuevo
        for li in container.select("li.wp-manga-chapter"):
            a = li.find("a")
            if not a or not a.get_text(strip=True):
                continue
            # “Capítulo 54.1” / “Chapter 12”
            num = parse_chapter_from_text(a.get_text(" ", strip=True))
            if num is not None:
                return num

    # 2) Fallback: buscar anchors que contengan “capitulo” o “chapter”
    for a in soup.find_all("a"):
        t = a.get_text(" ", strip=True)
        if not t:
            continue
        low = t.lower()
        if any(w in low for w in CHAPTER_WORDS):
            num = parse_chapter_from_text(t)
            if num is not None:
                return num

    # 3) Ultimo recurso: extraer número desde la URL (…/capitulo-54-1/)
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(w in href for w in ["capitulo", "chapter", "/ch-"]):
            # capitulo-53-2, 53, 53-2, etc.
            m = re.search(r"(?:cap[ií]tulo|chapter|ch|ep|episode)[/_-]*([0-9]+(?:[-.][0-9]+)?)", href)
            if m:
                return normalize_number(m.group(1))

    return None

def parse_m440_latest(html: str) -> Optional[float]:
    """
    m440.in: la página tiene una lista con <li class="EookRAWUYWz-lis"><h5>… #168 …</h5></li>
    Tomamos el primer li con esa clase y extraemos el número junto al '#'.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) H5 con '#'
    for h5 in soup.find_all("h5", class_="EookRAWUYWz"):
        text = h5.get_text(" ", strip=True)
        num = parse_chapter_from_text(text)
        if num is not None:
            return num

    # 2) li con clase EookRAWUYWz-lis
    for li in soup.select("li.EookRAWUYWz-lis"):
        text = li.get_text(" ", strip=True)
        num = parse_chapter_from_text(text)
        if num is not None:
            return num

    # 3) URLs con /manga/<slug>/<numero[-sub]>
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        m = re.search(r"/manga/[^/]+/([0-9]+(?:[_.-][0-9]+)?)", href)
        if m:
            raw = m.group(1).replace("_", ".")
            return normalize_number(raw)

    return None

def parse_zonatmo_latest(html: str) -> Optional[float]:
    """
    zonatmo: intenta extraer de enlaces o del texto tipo Chapter xx.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Buscar anchors de capítulos
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        h = a["href"]
        if t:
            num = parse_chapter_from_text(t)
            if num is not None:
                return num
        # Fallback: desde URL
        m = re.search(r"/chapter/([0-9]+(?:[-.][0-9]+)?)", h.lower())
        if m:
            return normalize_number(m.group(1))

    # Fallback global
    body_text = soup.get_text(" ", strip=True)
    num = parse_chapter_from_text(body_text)
    if num is not None:
        return num

    return None

def parse_animebbg_latest(html: str) -> Optional[float]:
    """
    animebbg: si te deja ver la lista de capítulos, cae como Madara/variación.
    """
    # Reutilizamos el parser madara; muchas instancias de WordPress usan estructuras parecidas
    n = parse_madara_latest(html)
    if n is not None:
        return n

    # Fallback: buscar '#numero'
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(True):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        m = re.search(r"#\s*(\d+(?:\.\d+)?(?:-\d+)?)", text)
        if m:
            return normalize_number(m.group(1))
    return None

# ------------------------------
# Router por dominio
# ------------------------------

def extract_latest_chapter(url: str, html: str) -> Optional[float]:
    d = domain_of(url)

    if "bokugents.com" in d:
        return parse_madara_latest(html)
    if "mangasnosekai.com" in d:
        return parse_madara_latest(html)
    if "m440.in" in d:
        return parse_m440_latest(html)
    if "zonatmo.com" in d:
        return parse_zonatmo_latest(html)
    if "animebbg.net" in d:
        return parse_animebbg_latest(html)

    # genérico (último recurso)
    return parse_madara_latest(html) or parse_m440_latest(html) or parse_zonatmo_latest(html)

# ------------------------------
# Core
# ------------------------------

@dataclass
class SeriesItem:
    name: str
    site: str
    url: str
    last_chapter: Optional[float]

def load_series(path="series.yml"):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    items = []
    for it in data.get("series", []):
        items.append(
            SeriesItem(
                name=it.get("name", "").strip(),
                site=str(it.get("site", "")).strip(),
                url=it.get("url", "").strip(),
                last_chapter=it.get("last_chapter", None),
            )
        )
    return items

def save_series(items, path="series.yml"):
    out = {"series": []}
    for it in items:
        out["series"].append({
            "name": it.name,
            "site": it.site,
            "url": it.url,
            "last_chapter": it.last_chapter if it.last_chapter is not None else None,
        })
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, sort_keys=False, allow_unicode=True)

def main():
    items = load_series()
    changed = False

    for it in items:
        try:
            html = fetch_html(it.url)
        except Exception as e:
            log_warn(f"No se pudo obtener {it.url}: {e}")
            continue

        latest = extract_latest_chapter(it.url, html)
        if latest is None:
            log_warn(f"No pude encontrar capítulo en: {it.url} — «{it.name}»")
            continue

        # Comparar y avisar
        prev = it.last_chapter
        if prev is None or float(latest) > float(prev):
            print(f"[NUEVO] {it.name} — {prev if prev is not None else '0.0'} -> {latest}")
            it.last_chapter = float(latest)
            changed = True

    if changed:
        save_series(items)
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    main()

