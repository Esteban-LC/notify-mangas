#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import time
import random
import argparse
import urllib.parse as urlparse
from dataclasses import dataclass
from typing import Optional, List, Tuple

import yaml
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# =============================
# Config de red
# =============================

UA_LIST = [
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

CHAPTER_WORDS = ["capítulo", "capitulo", "chapter", "cap", "ch", "episodio", "episode"]

# =============================
# Utils
# =============================

def log_warn(msg: str):
    print(f"[WARN] {msg}")

def domain_of(url: str) -> str:
    return urlparse.urlparse(url).netloc.lower().strip()

def fetch_html(url: str) -> str:
    last_err = None
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    session.headers["User-Agent"] = random.choice(UA_LIST)

    for i in range(RETRIES):
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            if resp.status_code == 403:
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

def normalize_number(txt: str) -> Optional[float]:
    txt = txt.strip()

    # Primero intenta 166.5, 150, etc.
    m = re.search(r"(\d+(?:\.\d+)?)", txt)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # 49-1 -> 49.1 ; 7-20 -> 7.20
    m = re.search(r"(\d+)-(\d+)", txt)
    if m:
        left, right = m.group(1), m.group(2)
        try:
            return float(f"{left}.{right}")
        except ValueError:
            return None

    return None

def parse_chapter_from_text(text: str) -> Optional[float]:
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

# =============================
# Parsers por sitio
# =============================

def parse_madara_latest(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")

    # Estructura típica Madara
    container = soup.select_one(".listing-chapters_wrap ul.main") \
        or soup.select_one(".page-content-listing .listing-chapters_wrap ul.main")

    if container:
        for li in container.select("li.wp-manga-chapter"):
            a = li.find("a")
            if not a:
                continue
            num = parse_chapter_from_text(a.get_text(" ", strip=True))
            if num is not None:
                return num

    # Fallback por textos
    for a in soup.find_all("a"):
        t = a.get_text(" ", strip=True)
        if not t:
            continue
        if any(w in t.lower() for w in CHAPTER_WORDS):
            num = parse_chapter_from_text(t)
            if num is not None:
                return num

    # Fallback por URL
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(w in href for w in ["capitulo", "chapter", "/ch-"]):
            m = re.search(r"(?:cap[ií]tulo|chapter|ch|ep|episode)[/_-]*([0-9]+(?:[-.][0-9]+)?)", href)
            if m:
                return normalize_number(m.group(1))
    return None

def parse_m440_latest(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")

    # 1) H5 con la clase que trae "#168"
    for h5 in soup.find_all("h5", class_="EookRAWUYWz"):
        num = parse_chapter_from_text(h5.get_text(" ", strip=True))
        if num is not None:
            return num

    # 2) LI por clase
    for li in soup.select("li.EookRAWUYWz-lis"):
        num = parse_chapter_from_text(li.get_text(" ", strip=True))
        if num is not None:
            return num

    # 3) URL con /manga/<slug>/<numero[-sub]>
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        m = re.search(r"/manga/[^/]+/([0-9]+(?:[_.-][0-9]+)?)", href)
        if m:
            raw = m.group(1).replace("_", ".")
            return normalize_number(raw)
    return None

def parse_zonatmo_latest(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")

    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True)
        if t:
            num = parse_chapter_from_text(t)
            if num is not None:
                return num
        m = re.search(r"/chapter/([0-9]+(?:[-.][0-9]+)?)", a["href"].lower())
        if m:
            return normalize_number(m.group(1))

    body = soup.get_text(" ", strip=True)
    num = parse_chapter_from_text(body)
    if num is not None:
        return num
    return None

def parse_animebbg_latest(html: str) -> Optional[float]:
    n = parse_madara_latest(html)
    if n is not None:
        return n

    soup = BeautifulSoup(html, "html.parser")
    for el in soup.find_all(True):
        m = re.search(r"#\s*(\d+(?:\.\d+)?(?:-\d+)?)", el.get_text(" ", strip=True) or "")
        if m:
            return normalize_number(m.group(1))
    return None

def extract_latest_chapter(url: str, html: str) -> Optional[float]:
    d = domain_of(url)
    if "bokugents.com" in d or "mangasnosekai.com" in d:
        return parse_madara_latest(html)
    if "m440.in" in d:
        return parse_m440_latest(html)
    if "zonatmo.com" in d:
        return parse_zonatmo_latest(html)
    if "animebbg.net" in d:
        return parse_animebbg_latest(html)
    # último recurso
    return parse_madara_latest(html) or parse_m440_latest(html) or parse_zonatmo_latest(html)

# =============================
# Lectura/Escritura YAML
# =============================

@dataclass
class SeriesItem:
    name: str
    site: str
    url: str
    last_chapter: Optional[float]

@dataclass
class LibraryDoc:
    path: Path
    root_key: str  # normalmente "series"
    items: List[SeriesItem]

def detect_yaml_path(cli_path: Optional[str]) -> Path:
    if cli_path:
        p = Path(cli_path)
        if not p.exists():
            raise FileNotFoundError(f"No existe el archivo: {cli_path}")
        return p
    # autodetección
    for candidate in ("manga_library.yml", "series.yml"):
        p = Path(candidate)
        if p.exists():
            return p
    raise FileNotFoundError("No se encontró ni 'manga_library.yml' ni 'series.yml'. Pásalo como argumento.")

def load_library(path: Path) -> LibraryDoc:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # Soportamos dos formas: {series: [...]} o lista directa
    if isinstance(data, dict):
        if "series" in data and isinstance(data["series"], list):
            raw_list = data["series"]
            root_key = "series"
        else:
            # si hay una sola clave con una lista, úsala
            keys_with_list = [k for k, v in data.items() if isinstance(v, list)]
            if len(keys_with_list) == 1:
                root_key = keys_with_list[0]
                raw_list = data[root_key]
            else:
                # fallback: crea root series
                root_key = "series"
                raw_list = []
    elif isinstance(data, list):
        root_key = "series"
        raw_list = data
    else:
        root_key = "series"
        raw_list = []

    items: List[SeriesItem] = []
    for it in raw_list:
        if not isinstance(it, dict):
            continue
        items.append(SeriesItem(
            name=str(it.get("name", "")).strip(),
            site=str(it.get("site", "")).strip(),
            url=str(it.get("url", "")).strip(),
            last_chapter=it.get("last_chapter", None),
        ))
    return LibraryDoc(path=path, root_key=root_key, items=items)

def save_library(doc: LibraryDoc):
    out_list = []
    for it in doc.items:
        out_list.append({
            "name": it.name,
            "site": it.site,
            "url": it.url,
            "last_chapter": it.last_chapter if it.last_chapter is not None else None,
        })

    # Respetar la raíz original
    data = {doc.root_key: out_list}
    with doc.path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

# =============================
# Main
# =============================

def main():
    ap = argparse.ArgumentParser(description="Scraper unificado de capítulos.")
    ap.add_argument("yaml_path", nargs="?", help="Ruta del YAML (p. ej. manga_library.yml).")
    args = ap.parse_args()

    yaml_path = detect_yaml_path(args.yaml_path)
    lib = load_library(yaml_path)

    changed = False
    for it in lib.items:
        if not it.url:
            continue
        try:
            html = fetch_html(it.url)
        except Exception as e:
            log_warn(f"No se pudo obtener {it.url}: {e}")
            continue

        latest = extract_latest_chapter(it.url, html)
        if latest is None:
            log_warn(f"No pude encontrar capítulo en: {it.url} — «{it.name}»")
            continue

        prev = it.last_chapter
        if prev is None or float(latest) > float(prev):
            print(f"[NUEVO] {it.name} — {prev if prev is not None else '0.0'} -> {latest}")
            it.last_chapter = float(latest)
            changed = True

    if changed:
        save_library(lib)
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    main()
