#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, json, time
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, urljoin, parse_qs

import requests
from bs4 import BeautifulSoup
import yaml

LIB_PATH = "manga_library.yml"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# ------------ Session helper (headers, proxy opcional) ------------
def build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    })
    proxy = os.getenv("PROXY_URL", "").strip()
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    s.timeout = 25
    return s

# ------------ Discord ------------
def send_discord(webhook_url: Optional[str], title: str, description: str, url: str, thumbnail: Optional[str] = None):
    if not webhook_url:
        return
    embed = {
        "title": title,
        "description": description,
        "url": url,
        "color": 0x5865F2,
        "footer": {"text": "Actualización de capítulos"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}
    payload = {"embeds": [embed]}
    try:
        r = requests.post(webhook_url, json=payload, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[WARN] No se pudo notificar a Discord: {e}", file=sys.stderr)

# ================================================================
# ==================== PARSERS POR SITIO =========================
# ================================================================

# -------- ANIMEBBG (XenForo) --------
CAP_RE = re.compile(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', re.I)

def _animebbg_find_max_in_page(soup: BeautifulSoup) -> Optional[float]:
    vals = []
    for a in soup.select('.structItem-title a'):
        txt = a.get_text(" ", strip=True)
        m = CAP_RE.search(txt)
        if m:
            try:
                vals.append(float(m.group(1)))
            except ValueError:
                pass
    return max(vals) if vals else None

def _animebbg_last_page(soup: BeautifulSoup) -> int:
    # botón "Último"
    last = soup.select_one('.pageNavSimple a.pageNavSimple-el--last')
    if last and last.has_attr('href'):
        try:
            q = urlparse(last['href']).query
            page = int(parse_qs(q).get('page', [1])[0])
            return page
        except Exception:
            pass
    # listado numerado
    pages = []
    for li in soup.select('ul.pageNav-main li a'):
        try:
            pages.append(int(li.get_text(strip=True)))
        except Exception:
            continue
    return max(pages) if pages else 1

def parse_animebbg(session: requests.Session, url: str) -> Optional[float]:
    # asegurar /capitulos
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    if not path.endswith('/capitulos'):
        url = urljoin(url if url.endswith('/') else url + '/', 'capitulos')
    r = session.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")

    best = _animebbg_find_max_in_page(soup) or float('-inf')
    last_page = _animebbg_last_page(soup)
    if last_page and last_page > 1:
        sep = '&' if ('?' in url) else '?'
        last_url = f"{url}{sep}page={last_page}"
        r2 = session.get(last_url); r2.raise_for_status()
        soup2 = BeautifulSoup(r2.text, "lxml")
        best_last = _animebbg_find_max_in_page(soup2)
        if best_last is not None:
            best = max(best, best_last)
    return None if best == float('-inf') else best

# -------- MangasNoSekai (WordPress) --------
def parse_mangasnosekai(session: requests.Session, url: str) -> Optional[float]:
    r = session.get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    vals = []
    for a in soup.select('.contenedor-capitulo-miniatura .text-sm, .contenedor-capitulo-miniatura a'):
        txt = a.get_text(" ", strip=True)
        m = re.search(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', txt, re.I)
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

# -------- m440.in (WordPress) --------
def parse_m440(session: requests.Session, url: str) -> Optional[float]:
    r = session.get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    vals = []
    for a in soup.select('a, .wp-block-latest-posts__post-title'):
        m = re.search(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', a.get_text(" ", strip=True), re.I)
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

# -------- zonatmo.com --------
def parse_zonatmo(session: requests.Session, url: str) -> Optional[float]:
    r = session.get(url); r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    vals = []
    # títulos colapsables “Capítulo X.YY …”
    for a in soup.select('.btn-collapse, .list-group-item h4, h4'):
        m = re.search(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', a.get_text(" ", strip=True), re.I)
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

# -------- MAPA DE PARSERS --------
PARSERS = {
    "animebbg.net": parse_animebbg,
    "www.animebbg.net": parse_animebbg,

    "mangasnosekai.com": parse_mangasnosekai,
    "www.mangasnosekai.com": parse_mangasnosekai,

    "m440.in": parse_m440,
    "www.m440.in": parse_m440,

    "zonatmo.com": parse_zonatmo,
    "www.zonatmo.com": parse_zonatmo,
}

# ================================================================
# ==================== LÓGICA PRINCIPAL ==========================
# ================================================================
def load_library(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []

def save_library(path: str, data: List[Dict[str, Any]]):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

def pick_parser(entry: Dict[str, Any]):
    # preferir 'site', sino deducir de la URL
    site = (entry.get("site") or "").strip().lower()
    if site in PARSERS:
        return PARSERS[site]
    netloc = urlparse(entry.get("url", "")).netloc.lower()
    return PARSERS.get(netloc)

def main():
    discord = os.getenv("DISCORD_WEBHOOK_URL", "").strip() or None
    session = build_session()

    lib = load_library(LIB_PATH)
    if not lib:
        print("No encuentro manga_library.yml (debe contener tu lista).")
        sys.exit(0)

    changed = False
    errors: List[str] = []
    for entry in lib:
        name = entry.get("name", "¿Sin nombre?")
        url = entry.get("url")
        if not url:
            continue

        parser = pick_parser(entry)
        if not parser:
            errors.append(f"No tengo parser para {urlparse(url).netloc} en ‘{name}’")
            continue

        try:
            latest = parser(session, url)
        except requests.HTTPError as e:
            errors.append(f"{name}: HTTP Error: {e} — {url}")
            continue
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e} — {url}")
            continue

        if latest is None:
            # no se pudo extraer número
            continue

        prev = entry.get("last_chapter")
        # Bootstrap: si nunca se ha guardado, persistir sin notificar
        if prev is None:
            entry["last_chapter"] = latest
            changed = True
            print(f"[BOOTSTRAP] {name} -> {latest}")
            continue

        try:
            prev_num = float(prev)
        except Exception:
            prev_num = None

        if prev_num is None or latest > prev_num:
            # actualizar y notificar
            entry["last_chapter"] = latest
            changed = True
            title = f"{name} — Capítulo {latest:g}"
            desc = f"Nuevo capítulo detectado: **{latest:g}**"
            send_discord(discord, title, desc, url)
            print(f"[NUEVO] {title}")
        else:
            print(f"[OK] {name}: sin cambios (último {prev_num:g})")

    if changed:
        save_library(LIB_PATH, lib)
    else:
        print("Sin novedades.")

    # Reporte de errores al final
    if errors and discord:
        msg = "\n".join(errors[:10])
        send_discord(discord, "Errores de scraping", msg, "https://github.com/")
    for e in errors:
        print("Error:", e)

if __name__ == "__main__":
    main()
