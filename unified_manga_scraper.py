#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scraper unificado para distintas fuentes de manga/manhwa.
- Carga manga_library.yml
- Consulta el capítulo más reciente por sitio
- Notifica a Discord cuando hay novedades
- Actualiza last_chapter y guarda el YAML

Soporta:
- Plantillas Madara (bokugents.com, mangasnosekai.com, m440.in, etc.)
- XenForo simple (animebbg.net) – heurística basada en “Capítulo X”
- Genérico por regex (zonatmo.com y similares)

Secrets opcionales (GitHub Actions):
- DISCORD_WEBHOOK_URL
- PROXY_URL (http/https)
- EXTRA_COOKIES_JSON  -> p.ej:
  {
    "animebbg.net": {"cf_clearance":"...", "__cf_bm":"..."},
    "m440.in": {"__cf_bm":"..."}
  }
"""

import os
import re
import json
import time
import random
import math
import yaml
import requests
from typing import Any, Dict, List, Tuple
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MANGA_FILE = "manga_library.yml"

# ------------------------------
# Utilidades de YAML
# ------------------------------
def load_library() -> Dict[str, Any]:
    if not os.path.exists(MANGA_FILE):
        return {"series": []}
    with open(MANGA_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # Acepta tope como lista
    if isinstance(data, list):
        data = {"series": data}
    if "series" not in data or not isinstance(data["series"], list):
        data["series"] = []
    # Normaliza campos mínimos
    for it in data["series"]:
        it.setdefault("name", "")
        it.setdefault("site", "")
        it.setdefault("url", "")
        it.setdefault("last_chapter", 0.0)
    return data


def save_library(data: Dict[str, Any]) -> None:
    with open(MANGA_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            width=120,
            default_flow_style=False,
        )


def coerce_float(x: Any) -> float:
    if x is None or x == "":
        return 0.0
    try:
        return float(x)
    except Exception:
        try:
            # capturar algo tipo "54,1"
            return float(str(x).replace(",", "."))
        except Exception:
            return 0.0


# ------------------------------
# Sesión HTTP robusta
# ------------------------------
def make_session() -> requests.Session:
    s = requests.Session()

    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=[403, 429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.mount("https://", HTTPAdapter(max_retries=retry))

    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    })

    # Cookies opcionales
    cookies_json = os.getenv("EXTRA_COOKIES_JSON", "").strip()
    if cookies_json:
        try:
            jar = json.loads(cookies_json)
            for domain, pairs in jar.items():
                for k, v in pairs.items():
                    # Nota: domain sin esquema
                    d = domain.replace("http://", "").replace("https://", "").strip("/")
                    s.cookies.set(k, v, domain=d)
        except Exception as e:
            print("[WARN] EXTRA_COOKIES_JSON inválido:", e)

    # Proxy opcional
    proxy_url = os.getenv("PROXY_URL", "").strip()
    if proxy_url:
        s.proxies.update({"http": proxy_url, "https": proxy_url})

    return s


def fetch_html(session: requests.Session, url: str, referer: str = None) -> BeautifulSoup:
    if referer:
        session.headers["Referer"] = referer
    else:
        session.headers.pop("Referer", None)

    # peq. delay para no parecer bot completely
    time.sleep(random.uniform(0.6, 1.2))
    r = session.get(url, timeout=25)
    if r.status_code == 403:
        print(f"[WARN] 403 Forbidden en {url}")
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ------------------------------
# Parsers
# ------------------------------

MADARA_CHAPTER_RE = re.compile(r"(\d+(?:\.\d+)?)")

def parse_madara_latest(soup: BeautifulSoup) -> float:
    """
    Plantilla Madara:
    - ul.main.version-chap li.wp-manga-chapter a (más nuevo arriba)
    """
    ul = soup.select_one("ul.main.version-chap.active") or soup.select_one("ul.main.version-chap")
    if ul:
        links = ul.select("li.wp-manga-chapter a")
    else:
        links = soup.select("li.wp-manga-chapter a")

    if not links:
        # Otras variantes de Madara:
        links = soup.select("div.listing-chapters_wrap a, div.chapter-list a")

    if not links:
        raise ValueError("No se encontraron capítulos (Madara).")

    latest_text = links[0].get_text(strip=True)
    m = MADARA_CHAPTER_RE.search(latest_text)
    if not m:
        # último intento: escanear todos y quedarnos con el mayor
        nums = []
        for a in links[:50]:
            t = a.get_text(strip=True)
            mm = MADARA_CHAPTER_RE.search(t)
            if mm:
                try:
                    nums.append(float(mm.group(1)))
                except Exception:
                    pass
        if not nums:
            raise ValueError(f"No pude extraer número de capítulo de: {latest_text!r}")
        return max(nums)
    return float(m.group(1))


def parse_xenforo_latest(soup: BeautifulSoup) -> float:
    """
    Heurística simple para XenForo (animebbg):
    Busca “Capítulo X[.Y]” en títulos/enlaces y toma el mayor.
    """
    texts = []
    # títulos típicos
    texts += [a.get_text(" ", strip=True) for a in soup.select(".structItem-title a")]
    # por si acaso
    texts += [a.get_text(" ", strip=True) for a in soup.select("a")]

    nums = []
    for t in texts:
        # prioriza textos que contengan 'Capítulo'
        if "capítulo" in t.lower():
            m = MADARA_CHAPTER_RE.search(t)
            if m:
                try:
                    nums.append(float(m.group(1)))
                except Exception:
                    pass

    if not nums:
        # como fallback, cualquier número en anchors
        for t in texts:
            m = MADARA_CHAPTER_RE.search(t)
            if m:
                try:
                    nums.append(float(m.group(1)))
                except Exception:
                    pass

    if not nums:
        raise ValueError("No hallé números de capítulo en XenForo.")
    return max(nums)


def parse_generic_latest_by_regex(soup: BeautifulSoup) -> float:
    """
    Genérico: revisa anchors que contengan 'Capítulo' y toma el máximo.
    Sirve como fallback para sitios varios (zonatmo, etc.).
    """
    anchors = soup.select("a")
    nums = []
    for a in anchors[:400]:
        txt = a.get_text(" ", strip=True)
        if not txt:
            continue
        if "capítulo" in txt.lower():
            m = MADARA_CHAPTER_RE.search(txt)
            if m:
                try:
                    nums.append(float(m.group(1)))
                except Exception:
                    pass
    if not nums:
        # último intento con cualquier número
        for a in anchors[:400]:
            txt = a.get_text(" ", strip=True)
            m = MADARA_CHAPTER_RE.search(txt or "")
            if m:
                try:
                    nums.append(float(m.group(1)))
                except Exception:
                    pass
    if not nums:
        raise ValueError("No pude extraer capítulo por regex genérico.")
    return max(nums)


def choose_parser(domain: str):
    d = domain.lower()
    # Madara
    if d.endswith("bokugents.com"):
        return parse_madara_latest
    if d.endswith("mangasnosekai.com"):
        return parse_madara_latest
    if d.endswith("m440.in"):
        return parse_madara_latest

    # XenForo simple
    if d.endswith("animebbg.net"):
        return parse_xenforo_latest

    # Fallback genérico
    return parse_generic_latest_by_regex


def get_latest_chapter(session: requests.Session, item: Dict[str, Any]) -> float:
    url = item.get("url", "").strip()
    if not url:
        raise ValueError("Item sin URL.")

    site = (item.get("site") or urlparse(url).netloc).lower()
    site = site.replace("https://", "").replace("http://", "").strip("/")

    soup = fetch_html(session, url)
    parser = choose_parser(site)
    return parser(soup)


# ------------------------------
# Discord
# ------------------------------
def discord_webhook(webhook_url: str, payload: Dict[str, Any]) -> None:
    r = requests.post(webhook_url, json=payload, timeout=20)
    # Webhook correcto devuelve 204 sin body
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Webhook HTTP {r.status_code}: {r.text[:200]}")


def build_discord_payload(novedades: List[Tuple[Dict[str, Any], float, float]]) -> Dict[str, Any]:
    if not novedades:
        return {
            "content": "Sin novedades."
        }

    lines = []
    for (it, old, new) in novedades:
        name = it.get("name", "¿?")
        url = it.get("url", "")
        lines.append(f"**{name}** — Capítulo nuevo: **{new}** (antes {old})\n{url}")

    text = "\n\n".join(lines)
    return {
        "embeds": [{
            "title": "📢 Nuevos capítulos detectados",
            "description": text[:4000],
            "color": 0x00B894
        }]
    }


# ------------------------------
# Main
# ------------------------------
def main():
    lib = load_library()
    series: List[Dict[str, Any]] = lib.get("series", [])
    session = make_session()

    novedades: List[Tuple[Dict[str, Any], float, float]] = []

    for item in series:
        name = item.get("name", "¿?")
        url = item.get("url", "")

        try:
            latest = get_latest_chapter(session, item)
        except requests.HTTPError as e:
            print("Error: ", f"No se pudo obtener {url}: {e}")
            print("Error: ", f"No pude leer HTML: {url}")
            continue
        except NotImplementedError as e:
            print(f"[WARN] {e}")
            continue
        except Exception as e:
            print("Error: ", f"Fallo al parsear {url}: {e}")
            continue

        last_seen = coerce_float(item.get("last_chapter"))
        if latest > last_seen:
            novedades.append((item, last_seen, latest))
            item["last_chapter"] = latest

    # Envío a Discord
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if webhook:
        try:
            payload = build_discord_payload(novedades)
            discord_webhook(webhook, payload)
        except Exception as e:
            print("[WARN] No pude actualizar el mensaje de estado en Discord:", e)

    # Mensaje consola
    if novedades:
        for (it, old, new) in novedades:
            print(f"[NUEVO] {it.get('name','¿?')} — {old} -> {new}")
    else:
        print("Sin novedades.")

    # Guardar YAML (si cambió)
    save_library(lib)


if __name__ == "__main__":
    main()
