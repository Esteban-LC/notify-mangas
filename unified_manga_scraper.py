#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scraper unificado de manga/manhwa.
- Carga y guarda manga_library.yml
- Soporta múltiples plantillas (Madara, XenForo, genérico)
- Detecta capítulos nuevos y los reporta a Discord
-v3
"""

import os, re, json, time, random, yaml, math, requests
from typing import Any, Dict, List, Tuple
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MANGA_FILE = "manga_library.yml"

# ===============================
# Utilidades YAML
# ===============================
def load_library() -> Dict[str, Any]:
    if not os.path.exists(MANGA_FILE):
        return {"series": []}
    with open(MANGA_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data, list):
        data = {"series": data}
    if "series" not in data or not isinstance(data["series"], list):
        data["series"] = []
    for it in data["series"]:
        it.setdefault("name", "")
        it.setdefault("site", "")
        it.setdefault("url", "")
        it.setdefault("last_chapter", 0.0)
    return data


def save_library(data: Dict[str, Any]) -> None:
    with open(MANGA_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            data, f, allow_unicode=True, sort_keys=False, width=120, default_flow_style=False
        )


def coerce_float(x: Any) -> float:
    if x is None or x == "":
        return 0.0
    try:
        return float(x)
    except Exception:
        try:
            return float(str(x).replace(",", "."))
        except Exception:
            return 0.0


# ===============================
# Sesión HTTP robusta (sin retry 403)
# ===============================
def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],  # <- 403 removido
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
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
                d = domain.replace("http://", "").replace("https://", "").strip("/")
                for k, v in pairs.items():
                    s.cookies.set(k, v, domain=d)
        except Exception as e:
            print("[WARN] EXTRA_COOKIES_JSON inválido:", e)

    # Proxy opcional
    proxy_url = os.getenv("PROXY_URL", "").strip()
    if proxy_url:
        s.proxies.update({"http": proxy_url, "https": proxy_url})

    return s


def fetch_html(session: requests.Session, url: str) -> BeautifulSoup:
    time.sleep(random.uniform(0.8, 1.6))
    r = session.get(url, timeout=25)
    if r.status_code == 403:
        print(f"[WARN] 403 Forbidden en {url}")
        raise requests.HTTPError(f"403 Forbidden en {url}")
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ===============================
# Parsers
# ===============================
MADARA_CHAPTER_RE = re.compile(r"(\d+(?:\.\d+)?)")

def parse_madara_latest(soup: BeautifulSoup) -> float:
    ul = soup.select_one("ul.main.version-chap.active") or soup.select_one("ul.main.version-chap")
    if ul:
        links = ul.select("li.wp-manga-chapter a")
    else:
        links = soup.select("li.wp-manga-chapter a")

    if not links:
        links = soup.select("div.listing-chapters_wrap a, div.chapter-list a")

    if not links:
        raise ValueError("No se encontraron capítulos (Madara).")

    latest_text = links[0].get_text(strip=True)
    m = MADARA_CHAPTER_RE.search(latest_text)
    if m:
        return float(m.group(1))

    # Fallback: mayor número visible
    nums = []
    for a in links[:50]:
        t = a.get_text(strip=True)
        mm = MADARA_CHAPTER_RE.search(t)
        if mm:
            try:
                nums.append(float(mm.group(1)))
            except:
                pass
    if not nums:
        raise ValueError(f"No pude extraer número de capítulo de: {latest_text!r}")
    return max(nums)


def parse_xenforo_latest(soup: BeautifulSoup) -> float:
    texts = [a.get_text(" ", strip=True) for a in soup.select(".structItem-title a")]
    texts += [a.get_text(" ", strip=True) for a in soup.select("a")]
    nums = []
    for t in texts:
        if "capítulo" in t.lower():
            m = MADARA_CHAPTER_RE.search(t)
            if m:
                nums.append(float(m.group(1)))
    if not nums:
        for t in texts:
            m = MADARA_CHAPTER_RE.search(t)
            if m:
                nums.append(float(m.group(1)))
    if not nums:
        raise ValueError("No hallé números de capítulo en XenForo.")
    return max(nums)


def parse_generic_latest_by_regex(soup: BeautifulSoup) -> float:
    anchors = soup.select("a")
    nums = []
    for a in anchors[:400]:
        txt = a.get_text(" ", strip=True)
        if "capítulo" in txt.lower():
            m = MADARA_CHAPTER_RE.search(txt)
            if m:
                nums.append(float(m.group(1)))
    if not nums:
        for a in anchors[:400]:
            txt = a.get_text(" ", strip=True)
            m = MADARA_CHAPTER_RE.search(txt or "")
            if m:
                nums.append(float(m.group(1)))
    if not nums:
        raise ValueError("No pude extraer capítulo genérico.")
    return max(nums)


def choose_parser(domain: str):
    d = domain.lower()
    if any(x in d for x in ["bokugents.com", "m440.in", "mangasnosekai.com"]):
        return parse_madara_latest
    if "animebbg.net" in d:
        return parse_xenforo_latest
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


# ===============================
# Discord
# ===============================
def discord_webhook(webhook_url: str, payload: Dict[str, Any]) -> None:
    r = requests.post(webhook_url, json=payload, timeout=20)
    if r.status_code not in (200, 204):
        raise RuntimeError(f"Webhook HTTP {r.status_code}: {r.text[:200]}")


def build_discord_payload(novedades: List[Tuple[Dict[str, Any], float, float]]) -> Dict[str, Any]:
    if not novedades:
        return {"content": "Sin novedades."}
    lines = []
    for (it, old, new) in novedades:
        name = it.get("name", "¿?")
        url = it.get("url", "")
        lines.append(f"**{name}** — Capítulo nuevo: **{new}** (antes {old})\n{url}")
    return {
        "embeds": [{
            "title": "📢 Nuevos capítulos detectados",
            "description": "\n\n".join(lines)[:4000],
            "color": 0x00B894
        }]
    }


# ===============================
# Main
# ===============================
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
            continue
        except Exception as e:
            print("Error: ", f"Fallo al parsear {url}: {e}")
            continue

        last_seen = coerce_float(item.get("last_chapter"))
        if latest > last_seen:
            novedades.append((item, last_seen, latest))
            item["last_chapter"] = latest

    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if webhook:
        try:
            payload = build_discord_payload(novedades)
            discord_webhook(webhook, payload)
        except Exception as e:
            print("[WARN] No pude actualizar Discord:", e)

    if novedades:
        for (it, old, new) in novedades:
            print(f"[NUEVO] {it.get('name','¿?')} — {old} -> {new}")
    else:
        print("Sin novedades.")

    save_library(lib)


if __name__ == "__main__":
    main()
