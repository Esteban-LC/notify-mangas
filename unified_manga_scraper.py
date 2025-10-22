#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import os
import time
import random
import argparse
import urllib.parse as urlparse
from dataclasses import dataclass
from typing import Optional, List

import yaml
import requests
from bs4 import BeautifulSoup
from pathlib import Path

# =============================
# Config
# =============================

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; rv:125.0) Gecko/20100101 Firefox/125.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    # pseudo client hints
    "sec-ch-ua": '"Chromium";v="126", "Not.A/Brand";v="24", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

TIMEOUT = 25
RETRIES = 3
BACKOFF = 2.0

CHAPTER_WORDS = ["capítulo", "capitulo", "chapter", "cap", "ch", "episodio", "episode"]

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
ALWAYS_NOTIFY = os.getenv("ALWAYS_NOTIFY", "false").lower() in {"1", "true", "yes", "y"}

# =============================
# Utils
# =============================

def log_warn(msg: str):
    print(f"[WARN] {msg}")

def domain_of(url: str) -> str:
    return urlparse.urlparse(url).netloc.lower().strip()

def base_referer(url: str) -> str:
    p = urlparse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"

def fetch_html(url: str) -> str:
    last_err = None
    session = requests.Session()
    # cookies básicos por si el sitio revisa “primera visita”
    session.cookies.set("cf_clearance", "", domain=domain_of(url))
    for i in range(RETRIES):
        headers = dict(BASE_HEADERS)
        headers["User-Agent"] = random.choice(UA_LIST)
        headers["Referer"] = base_referer(url)

        try:
            resp = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
            if resp.status_code == 403:
                last_err = Exception(f"403 Forbidden en {url}")
                time.sleep(BACKOFF * (i + 1))
                continue
            resp.raise_for_status()
            # algunos sitios devuelven vacío con 200; verifica tamaño
            if not resp.text or len(resp.text) < 512:
                last_err = Exception(f"Respuesta sospechosa (muy corta) en {url}")
                time.sleep(BACKOFF * (i + 1))
                continue
            return resp.text
        except Exception as e:
            last_err = e
            time.sleep(BACKOFF * (i + 1))
    raise last_err

def normalize_number(txt: str) -> Optional[float]:
    txt = txt.strip()
    m = re.search(r"(\d+(?:\.\d+)?)", txt)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"(\d+)[-_/\.](\d+)", txt)
    if m:
        left, right = m.group(1), m.group(2)
        try:
            return float(f"{left}.{right}")
        except ValueError:
            return None
    return None

def parse_chapter_from_text(text: str) -> Optional[float]:
    patterns = [
        r"#\s*(\d+(?:\.\d+)?(?:[-_]\d+)?)",
        r"(?:cap[ií]tulo|cap|ch(?:apter)?)\s*[:#]?\s*(\d+(?:\.\d+)?(?:[-_]\d+)?)",
        r"(?:episodio|episode)\s*[:#]?\s*(\d+(?:\.\d+)?(?:[-_]\d+)?)",
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
    container = soup.select_one(".listing-chapters_wrap ul.main") or \
                soup.select_one(".page-content-listing .listing-chapters_wrap ul.main")
    if container:
        for li in container.select("li.wp-manga-chapter"):
            a = li.find("a")
            if not a:
                continue
            num = parse_chapter_from_text(a.get_text(" ", strip=True))
            if num is not None:
                return num
    for a in soup.find_all("a"):
        t = a.get_text(" ", strip=True)
        if not t:
            continue
        if any(w in t.lower() for w in CHAPTER_WORDS):
            num = parse_chapter_from_text(t)
            if num is not None:
                return num
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(w in href for w in ["capitulo", "chapter", "/ch-"]):
            m = re.search(r"(?:cap[ií]tulo|chapter|ch|ep|episode)[/_-]*([0-9]+(?:[-.][0-9]+)?)", href)
            if m:
                return normalize_number(m.group(1))
    return None

def parse_m440_latest(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")
    for h5 in soup.find_all("h5", class_="EookRAWUYWz"):
        num = parse_chapter_from_text(h5.get_text(" ", strip=True))
        if num is not None:
            return num
    for li in soup.select("li.EookRAWUYWz-lis"):
        num = parse_chapter_from_text(li.get_text(" ", strip=True))
        if num is not None:
            return num
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
    return parse_madara_latest(html)

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
    return parse_madara_latest(html) or parse_m440_latest(html) or parse_zonatmo_latest(html)

# =============================
# YAML I/O
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
    root_key: str
    items: List[SeriesItem]

def detect_yaml_path(cli_path: Optional[str]) -> Path:
    if cli_path:
        p = Path(cli_path)
        if not p.exists():
            raise FileNotFoundError(f"No existe el archivo: {cli_path}")
        return p
    for candidate in ("manga_library.yml", "series.yml"):
        p = Path(candidate)
        if p.exists():
            return p
    raise FileNotFoundError("No se encontró ni 'manga_library.yml' ni 'series.yml'.")

def load_library(path: Path) -> LibraryDoc:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data, dict):
        if "series" in data and isinstance(data["series"], list):
            raw_list = data["series"]; root_key = "series"
        else:
            keys_with_list = [k for k, v in data.items() if isinstance(v, list)]
            root_key = keys_with_list[0] if keys_with_list else "series"
            raw_list = data.get(root_key, [])
    elif isinstance(data, list):
        root_key, raw_list = "series", data
    else:
        root_key, raw_list = "series", []
    items: List[SeriesItem] = []
    for it in raw_list:
        if not isinstance(it, dict): continue
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
    data = {doc.root_key: out_list}
    with doc.path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

# =============================
# Discord
# =============================

def post_to_discord(content: str):
    if not DISCORD_WEBHOOK:
        print("[WARN] DISCORD_WEBHOOK no configurado; no se enviará notificación.")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": content[:2000]}, timeout=15)
        if r.status_code >= 300:
            print(f"[WARN] Discord devolvió {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[WARN] Error enviando a Discord: {e}")

def format_updates(upds: List[str]) -> str:
    if not upds: return ""
    return "**Novedades de mangas:**\n" + "\n".join(f"• {u}" for u in upds)

def format_errors(errs: List[str]) -> str:
    if not errs: return ""
    hdr = "**Avisos/errores:**"
    # reduce ruido: solo primeros 15
    body = "\n".join(f"• {e}" for e in errs[:15])
    more = "" if len(errs) <= 15 else f"\n… y {len(errs)-15} más."
    return f"{hdr}\n{body}{more}"

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
    updates: List[str] = []
    errors: List[str] = []

    for it in lib.items:
        if not it.url:
            continue
        try:
            html = fetch_html(it.url)
        except Exception as e:
            msg = f"No se pudo obtener {it.url}: {e}"
            log_warn(msg); errors.append(msg)
            continue

        latest = extract_latest_chapter(it.url, html)
        if latest is None:
            msg = f"No pude encontrar capítulo en: {it.url} — «{it.name}»"
            log_warn(msg); errors.append(msg)
            continue

        prev = it.last_chapter
        if prev is None or float(latest) > float(prev):
            line = f"{it.name} — {prev if prev is not None else '0.0'} → {latest}"
            print(f"[NUEVO] {line}")
            it.last_chapter = float(latest)
            updates.append(line)
            changed = True

        # peq. pausa aleatoria para no quemar al host
        time.sleep(random.uniform(0.4, 1.1))

    if changed:
        save_library(lib)

    # ----- Notificación a Discord -----
    if updates:
        post_to_discord(format_updates(updates))
        if errors:
            post_to_discord(format_errors(errors))
    else:
        if errors:
            post_to_discord(format_errors(errors))
        elif ALWAYS_NOTIFY:
            post_to_discord("Sin novedades.")

    if not changed:
        print("Sin novedades.")

if __name__ == "__main__":
    main()
