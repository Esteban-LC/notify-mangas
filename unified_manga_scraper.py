#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx
import yaml
from bs4 import BeautifulSoup

LIB_PATH = "manga_library.yml"   # <<--- tu archivo
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}
JS_SITES = (
    "m440.in",
    "mangasnosekai.com",
    "zonatmo.com",
    "animebbg.net",
)

# -------------------- utils discord --------------------

def _get_discord_webhook() -> Optional[str]:
    return os.getenv("DISCORD_WEBHOOK") or os.getenv("DISCORD_WEBHOOK_URL")

def send_discord(content: str, embeds: list[dict[str, Any]] | None = None) -> None:
    webhook = _get_discord_webhook()
    if not webhook:
        print("[WARN] DISCORD_WEBHOOK no configurado; no se enviará notificación.")
        return
    payload: dict[str, Any] = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(webhook, json=payload)
            r.raise_for_status()
    except Exception as e:
        print(f"[WARN] Falló el envío a Discord: {e}")

# -------------------- YAML --------------------

@dataclass
class Serie:
    name: str
    site: str
    url: str
    last_chapter: Optional[float]

def load_series(path: str = LIB_PATH) -> list[Serie]:
    if not os.path.exists(path):
        print(f"[INFO] No existe {path}; creando estructura vacía.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items = []
    for s in data.get("series", []):
        items.append(
            Serie(
                name=s.get("name", "").strip(),
                site=s.get("site", "").strip(),
                url=s.get("url", "").strip(),
                last_chapter=float(s["last_chapter"]) if s.get("last_chapter") is not None else None,
            )
        )
    return items

def save_series(series: list[Serie], path: str = LIB_PATH) -> None:
    data = {"series": []}
    for s in series:
        data["series"].append(
            {
                "name": s.name,
                "site": s.site,
                "url": s.url,
                "last_chapter": s.last_chapter,
            }
        )
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

# -------------------- HTTP/Playwright --------------------

def _cookie_for(host: str, extra_cookie_map: dict[str, str]) -> Optional[str]:
    for k, v in extra_cookie_map.items():
        if k in host:
            return v
    return None

async def _fetch_playwright(url: str, cookies_header: Optional[str] = None, proxy: Optional[str] = None) -> str:
    from playwright.async_api import async_playwright

    launch_args = {"headless": True}
    if proxy:
        launch_args["proxy"] = {"server": proxy}

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="es-ES",
            extra_http_headers={"Accept-Language": DEFAULT_HEADERS["Accept-Language"]},
        )
        if cookies_header:
            cookies = []
            for part in cookies_header.split(";"):
                part = part.strip()
                if not part or "=" not in part:
                    continue
                name, value = part.split("=", 1)
                cookies.append({"name": name.strip(), "value": value.strip(), "domain": "."})
            with contextlib.suppress(Exception):
                await context.add_cookies(cookies)

        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # da un respiro a los scripts para poblar DOM
        await page.wait_for_timeout(1500)
        html = await page.content()
        await browser.close()
        return html

def fetch_html(url: str) -> str:
    """
    httpx -> si 403 o sitio JS -> Playwright.
    Usa PROXY_URL y EXTRA_COOKIES_JSON cuando existan.
    """
    proxy_url = os.getenv("PROXY_URL") or None
    extra_cookies_json = os.getenv("EXTRA_COOKIES_JSON") or None

    parsed_host = url.split("/")[2]
    cookie_map: dict[str, str] = {}
    if extra_cookies_json:
        with contextlib.suppress(Exception):
            cookie_map = json.loads(extra_cookies_json)

    cookies_header = _cookie_for(parsed_host, cookie_map)
    headers = DEFAULT_HEADERS.copy()
    if "animebbg.net" in parsed_host:
        headers["Referer"] = "https://animebbg.net/"

    # 1) httpx
    try:
        with httpx.Client(follow_redirects=True, headers=headers, timeout=30, proxies=proxy_url) as client:
            if cookies_header:
                headers2 = headers.copy()
                headers2["Cookie"] = cookies_header
                client.headers.update(headers2)
            r = client.get(url)
            if r.status_code == 200 and "<html" in r.text.lower():
                return r.text
            if r.status_code == 403:
                raise httpx.HTTPStatusError("403", request=r.request, response=r)
    except Exception as e:
        # 2) Fallback JS
        if any(h in parsed_host for h in JS_SITES):
            return asyncio.run(_fetch_playwright(url, cookies_header=cookies_header, proxy=proxy_url))
        raise e

    # 3) sitios JS declarados (aunque httpx devolviera 200, a veces es HTML vacío)
    if any(h in parsed_host for h in JS_SITES):
        return asyncio.run(_fetch_playwright(url, cookies_header=cookies_header, proxy=proxy_url))
    return r.text  # type: ignore[name-defined]

# -------------------- Parsers --------------------

CAP_RE = re.compile(r"(\d+(?:\.\d+)?)")

def _max_cap_from_numbers(texts: list[str]) -> Optional[float]:
    best: Optional[float] = None
    for t in texts:
        for m in CAP_RE.finditer(t):
            try:
                val = float(m.group(1))
                if best is None or val > best:
                    best = val
            except Exception:
                pass
    return best

def parse_m440(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    for h5 in soup.select("li.EookRAWUYWz-lis h5"):
        texts.append(h5.get_text(" ", strip=True))
    if not texts:
        # fallback amplio
        texts = [soup.get_text(" ", strip=True)]
    return _max_cap_from_numbers(texts)

def parse_msk(url: str) -> Optional[float]:
    # mangasnosekai.com
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    # listas típicas: .wp-manga-chapter, .chapter-item, etc.
    for a in soup.select("li.wp-manga-chapter a, li.chapter-item a, .listing-chapters a, .chapters-list a"):
        texts.append(a.get_text(" ", strip=True))
    if not texts:
        texts = [soup.get_text(" ", strip=True)]
    return _max_cap_from_numbers(texts)

def parse_zonatmo(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if "library" in href or "chapter" in href or re.search(r"/\d", href):
            texts.append(a.get_text(" ", strip=True))
    if not texts:
        texts = [soup.get_text(" ", strip=True)]
    return _max_cap_from_numbers(texts)

def parse_animebbg(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    for a in soup.select("ul li a, .list-group a, a"):
        t = a.get_text(" ", strip=True)
        h = a.get("href", "")
        if re.search(r"\d", t) or re.search(r"\d", h):
            texts.append(t)
    if not texts:
        texts = [soup.get_text(" ", strip=True)]
    return _max_cap_from_numbers(texts)

def parse_bokugents(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    # suelen usar listas tipo eplister o cards
    for a in soup.select(".eplister a, .cl, .list a, .chapter a, a"):
        texts.append(a.get_text(" ", strip=True))
    if not texts:
        texts = [soup.get_text(" ", strip=True)]
    return _max_cap_from_numbers(texts)

def pick_parser(site: str) -> Callable[[str], Optional[float]]:
    host = (site or "").lower()
    if "m440.in" in host:
        return parse_m440
    if "mangasnosekai.com" in host:
        return parse_msk
    if "zonatmo.com" in host:
        return parse_zonatmo
    if "animebbg.net" in host:
        return parse_animebbg
    if "bokugents.com" in host:
        return parse_bokugents
    # genérico: intenta con Playwright y busca números
    return parse_bokugents

# -------------------- Main --------------------

def main() -> None:
    series = load_series(LIB_PATH)
    if not series:
        print("[INFO] No hay series en manga_library.yml")
        return

    updates: list[str] = []
    warnings: list[str] = []

    for s in series:
        parser = pick_parser(s.site or s.url)
        try:
            new_ch = parser(s.url)
            if new_ch is None:
                warnings.append(f"No pude encontrar capítulo en: {s.url} — «{s.name}»")
                continue

            if (s.last_chapter is None) or (new_ch > float(s.last_chapter)):
                updates.append(f"**{s.name}** — {s.last_chapter or 0} -> **{new_ch}**\n<{s.url}>")
                s.last_chapter = new_ch
            else:
                print(f"[OK] Sin cambios: {s.name} (último {s.last_chapter})")

        except httpx.HTTPStatusError as e:
            warnings.append(f"No se pudo obtener {s.url}: {e}")
        except Exception as e:
            warnings.append(f"Error al parsear {s.url}: {e}")

    # guardar si cambió algo
    try:
        save_series(series, LIB_PATH)
    except Exception as e:
        warnings.append(f"No pude guardar {LIB_PATH}: {e}")

    # Notificación a Discord
    always_notify = (os.getenv("ALWAYS_NOTIFY") or "false").lower() == "true"
    if updates or warnings or always_notify:
        content = "Sin novedades." if not updates else "Novedades:"
        embeds: list[dict[str, Any]] = []
        if updates:
            embeds.append({
                "title": "Series con capítulos nuevos",
                "description": "\n\n".join(updates)[:4000]
            })
        if warnings:
            embeds.append({
                "title": "Avisos/errores",
                "description": "\n".join(f"• {w}" for w in warnings)[:4000]
            })
        send_discord(content, embeds)
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
