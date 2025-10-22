#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, json, time
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, urljoin, parse_qs

import requests
import cloudscraper
from bs4 import BeautifulSoup
import yaml

LIB_PATH = "manga_library.yml"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

# ========= util: lectura de cookies por dominio desde secret =========
# Formato del secret EXTRA_COOKIES_JSON:
# {
#   "animebbg.net": "xf_session=...; xf_user=...",
#   "zonatmo.com": "cookie1=valor; cookie2=valor"
# }
def load_extra_cookies() -> Dict[str, Dict[str, str]]:
    raw = os.getenv("EXTRA_COOKIES_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        parsed: Dict[str, Dict[str, str]] = {}
        for dom, cookie_str in data.items():
            jar: Dict[str, str] = {}
            for pair in cookie_str.split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    jar[k.strip()] = v.strip()
            parsed[dom.lower()] = jar
        return parsed
    except Exception:
        return {}

# =================== Sesiones (requests y cloudscraper) ===================
def build_requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
        "Upgrade-Insecure-Requests": "1",
    })
    proxy = os.getenv("PROXY_URL", "").strip()
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    s.timeout = 25
    return s

def build_cf_session() -> requests.Session:
    # cloudscraper ya aplica headers/JA3/anti-bot
    s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9,en;q=0.8"})
    proxy = os.getenv("PROXY_URL", "").strip()
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s

def attach_domain_cookies(sess: requests.Session, netloc: str, cookies_map: Dict[str, Dict[str, str]]):
    jar = cookies_map.get(netloc.lower()) or cookies_map.get(netloc.lower().lstrip("www."))
    if not jar: 
        return
    for k, v in jar.items():
        sess.cookies.set(k, v, domain="." + netloc)

# =================== GET robusto ===================
def smart_get(primary: requests.Session, url: str, cookies_map: Dict[str, Dict[str, str]]) -> requests.Response:
    netloc = urlparse(url).netloc.lower()
    # intentamos con requests (+cookies si hay)
    attach_domain_cookies(primary, netloc, cookies_map)
    r = primary.get(url)
    if r.status_code != 403 and r.status_code < 500:
        r.raise_for_status()
        return r

    # si hay 403 ó 5xx, reintentar con cloudscraper
    cf = build_cf_session()
    attach_domain_cookies(cf, netloc, cookies_map)
    r2 = cf.get(url)
    r2.raise_for_status()
    return r2

# =================== Discord ===================
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
    try:
        r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[WARN] Discord: {e}", file=sys.stderr)

# =================== Parsers ===================
CAP_RE = re.compile(r'Cap[ií]tulo\s+(\d+(?:\.\d+)?)', re.I)

# --- animebbg (XenForo) ---
def _animebbg_find_max_in_page(soup: BeautifulSoup) -> Optional[float]:
    vals = []
    for a in soup.select('.structItem-title a'):
        txt = a.get_text(" ", strip=True)
        m = CAP_RE.search(txt)
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

def _animebbg_last_page(soup: BeautifulSoup) -> int:
    last = soup.select_one('.pageNavSimple a.pageNavSimple-el--last')
    if last and last.has_attr('href'):
        try:
            q = urlparse(last['href']).query
            return int(parse_qs(q).get('page', [1])[0])
        except Exception:
            pass
    pages = []
    for li in soup.select('ul.pageNav-main li a'):
        try: pages.append(int(li.get_text(strip=True)))
        except: pass
    return max(pages) if pages else 1

def parse_animebbg(sess: requests.Session, cookies_map: Dict[str, Dict[str, str]], url: str) -> Optional[float]:
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    if not path.endswith('/capitulos'):
        url = urljoin(url if url.endswith('/') else url + '/', 'capitulos')
    r = smart_get(sess, url, cookies_map)
    soup = BeautifulSoup(r.text, "lxml")

    best = _animebbg_find_max_in_page(soup) or float('-inf')
    last_page = _animebbg_last_page(soup)
    if last_page and last_page > 1:
        sep = '&' if ('?' in url) else '?'
        last_url = f"{url}{sep}page={last_page}"
        r2 = smart_get(sess, last_url, cookies_map)
        soup2 = BeautifulSoup(r2.text, "lxml")
        best_last = _animebbg_find_max_in_page(soup2)
        if best_last is not None:
            best = max(best, best_last)
    return None if best == float('-inf') else best

# --- Mangas No Sekai ---
def parse_mangasnosekai(sess: requests.Session, cookies_map: Dict[str, Dict[str, str]], url: str) -> Optional[float]:
    r = smart_get(sess, url, cookies_map)
    soup = BeautifulSoup(r.text, "lxml")
    vals = []
    for a in soup.select('.contenedor-capitulo-miniatura .text-sm, .contenedor-capitulo-miniatura a'):
        txt = a.get_text(" ", strip=True)
        m = CAP_RE.search(txt)
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

# --- m440.in ---
def parse_m440(sess: requests.Session, cookies_map: Dict[str, Dict[str, str]], url: str) -> Optional[float]:
    r = smart_get(sess, url, cookies_map)
    soup = BeautifulSoup(r.text, "lxml")
    vals = []
    for a in soup.select('a, .wp-block-latest-posts__post-title'):
        m = CAP_RE.search(a.get_text(" ", strip=True))
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

# --- zonatmo ---
def parse_zonatmo(sess: requests.Session, cookies_map: Dict[str, Dict[str, str]], url: str) -> Optional[float]:
    r = smart_get(sess, url, cookies_map)
    soup = BeautifulSoup(r.text, "lxml")
    vals = []
    for a in soup.select('.btn-collapse, .list-group-item h4, h4'):
        m = CAP_RE.search(a.get_text(" ", strip=True))
        if m:
            try: vals.append(float(m.group(1)))
            except: pass
    return max(vals) if vals else None

# --- mapa de parsers ---
PARSERS = {
    "animebbg.net": parse_animebbg, "www.animebbg.net": parse_animebbg,
    "mangasnosekai.com": parse_mangasnosekai, "www.mangasnosekai.com": parse_mangasnosekai,
    "m440.in": parse_m440, "www.m440.in": parse_m440,
    "zonatmo.com": parse_zonatmo, "www.zonatmo.com": parse_zonatmo,
}

# =================== Lógica principal ===================
def load_library(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path): return []
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or []

def save_library(path: str, data: List[Dict[str, Any]]):
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

def pick_parser(entry: Dict[str, Any]):
    site = (entry.get("site") or "").strip().lower()
    if site in PARSERS: return PARSERS[site]
    netloc = urlparse(entry.get("url", "")).netloc.lower()
    return PARSERS.get(netloc)

def main():
    discord = os.getenv("DISCORD_WEBHOOK_URL", "").strip() or None
    cookies_map = load_extra_cookies()
    sess = build_requests_session()

    lib = load_library(LIB_PATH)
    if not lib:
        print("No encuentro manga_library.yml (debe contener tu lista).")
        sys.exit(0)

    changed = False
    errors: List[str] = []
    for entry in lib:
        name = entry.get("name", "¿Sin nombre?")
        url = entry.get("url")
        if not url: continue

        parser = pick_parser(entry)
        if not parser:
            errors.append(f"No tengo parser para {urlparse(url).netloc} en ‘{name}’")
            continue

        try:
            latest = parser(sess, cookies_map, url)
        except requests.HTTPError as e:
            errors.append(f"{name}: HTTP Error: {e} — {url}")
            continue
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e} — {url}")
            continue

        if latest is None:
            continue

        prev = entry.get("last_chapter")
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

    if errors and discord:
        send_discord(discord, "Errores de scraping", "\n".join(errors[:10]), "https://github.com/")
    for e in errors:
        print("Error:", e)

if __name__ == "__main__":
    main()
