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

# ---------- Config ----------
LIB_PATH = "manga_library.yml"   # archivo con tus series

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# sitios que suelen requerir JS
JS_SITES = (
    "m440.in",
    "mangasnosekai.com",
    "zonatmo.com",
    "animebbg.net",
)

# ---------- Discord ----------

def _get_discord_webhook() -> Optional[str]:
    return os.getenv("DISCORD_WEBHOOK") or os.getenv("DISCORD_WEBHOOK_URL")

def send_discord(content: str, embeds: list[dict[str, Any]] | None = None) -> None:
    hook = _get_discord_webhook()
    if not hook:
        print("[WARN] DISCORD_WEBHOOK no configurado; no se enviará notificación.")
        return
    payload: dict[str, Any] = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    try:
        with httpx.Client(timeout=30) as c:
            r = c.post(hook, json=payload)
            r.raise_for_status()
    except Exception as e:
        print(f"[WARN] Falló el envío a Discord: {e}")

# ---------- YAML ----------

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
    res: list[Serie] = []
    for s in data.get("series", []):
        last = s.get("last_chapter")
        try:
            last = float(last) if last is not None else None
        except Exception:
            last = None
        res.append(Serie(s.get("name","").strip(), s.get("site","").strip(), s.get("url","").strip(), last))
    return res

def save_series(series: list[Serie], path: str = LIB_PATH) -> None:
    out = {"series": []}
    for s in series:
        out["series"].append(
            {
                "name": s.name,
                "site": s.site,
                "url": s.url,
                "last_chapter": s.last_chapter,
            }
        )
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(out, f, allow_unicode=True, sort_keys=False)

# ---------- HTTP + Playwright ----------

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
            # cookies best-effort; si no calza dominio exacto las ignora.
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
        await page.wait_for_timeout(1500)
        html = await page.content()
        await browser.close()
        return html

def fetch_html(url: str) -> str:
    """
    httpx primero; si 403 o sitio JS -> Playwright.
    Usa PROXY_URL (si está) y EXTRA_COOKIES_JSON (mapa dominio->string cookies).
    """
    proxy_url = os.getenv("PROXY_URL") or None
    extra_cookies_json = os.getenv("EXTRA_COOKIES_JSON") or None

    host = url.split("/")[2]
    cookie_map: dict[str, str] = {}
    if extra_cookies_json:
        with contextlib.suppress(Exception):
            cookie_map = json.loads(extra_cookies_json)
    cookies_header = _cookie_for(host, cookie_map)

    headers = DEFAULT_HEADERS.copy()
    if "animebbg.net" in host:
        headers["Referer"] = "https://animebbg.net/"

    # --- httpx (ATENCIÓN: httpx >= 0.28 usa 'proxy=', NO 'proxies=' ) ---
    try:
        with httpx.Client(
            follow_redirects=True,
            headers=headers,
            timeout=30,
            proxy=proxy_url,  # <----- FIX AQUI
        ) as client:
            if cookies_header:
                client.headers.update({"Cookie": cookies_header})
            r = client.get(url)
            if r.status_code == 200 and "<html" in r.text.lower():
                # ojo: hay sitios que devuelven HTML vacío si no hay JS
                if any(h in host for h in JS_SITES) and "script" in r.text.lower() and "chapter" not in r.text.lower():
                    raise httpx.HTTPStatusError("force-js", request=r.request, response=r)
                return r.text
            if r.status_code == 403:
                raise httpx.HTTPStatusError("403", request=r.request, response=r)
    except Exception:
        if any(h in host for h in JS_SITES):
            return asyncio.run(_fetch_playwright(url, cookies_header=cookies_header, proxy=proxy_url))
        raise

    # Por si el sitio requiere JS aunque devolvió 200
    if any(h in host for h in JS_SITES):
        return asyncio.run(_fetch_playwright(url, cookies_header=cookies_header, proxy=proxy_url))
    return r.text  # type: ignore[name-defined]

# ---------- Extracción robusta de capítulos ----------

# Busca números con contexto de capítulo
CAP_WITH_CONTEXT = re.compile(
    r"(?:cap(?:[íi]tulo)?|chapter|chap\.?|#)\s*([0-9]+(?:\.[0-9]+)?)",
    re.IGNORECASE,
)

# Cualquier número simple
ANY_NUMBER = re.compile(r"([0-9]+(?:\.[0-9]+)?)")

def _numbers_from_text(text: str) -> list[float]:
    """extrae candidatos con preferencia a contexto; evita años/ids grandes."""
    out: list[float] = []
    # 1) con contexto
    for m in CAP_WITH_CONTEXT.finditer(text):
        try:
            out.append(float(m.group(1)))
        except Exception:
            pass
    if out:
        return out
    # 2) sin contexto, pero filtrando basura (años/IDs)
    for m in ANY_NUMBER.finditer(text):
        try:
            val = float(m.group(1))
            if 1900 <= val <= 2100:   # años
                continue
            if val > 3000:            # IDs gigantes
                continue
            out.append(val)
        except Exception:
            pass
    return out

def _max_from_texts(texts: list[str]) -> Optional[float]:
    best: Optional[float] = None
    for t in texts:
        for v in _numbers_from_text(t):
            if best is None or v > best:
                best = v
    return best

# ---------- Parsers específicos (evitando get_text global) ----------

def parse_m440(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts: list[str] = []
    # patrón estable del sitio
    for h5 in soup.select("li.EookRAWUYWz-lis h5"):
        texts.append(h5.get_text(" ", strip=True))
    # también se puede tomar data-number cuando existe
    for a in soup.select("a[data-number]"):
        n = a.get("data-number")
        if n:
            texts.append(f"Cap {n}")
    if not texts:
        return None
    return _max_from_texts(texts)

def parse_msk(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts: list[str] = []
    for a in soup.select(
        "li.wp-manga-chapter a, li.chapter-item a, .listing-chapters a, .chapters-list a"
    ):
        texts.append(a.get_text(" ", strip=True))
    if not texts:
        return None
    return _max_from_texts(texts)

def parse_zonatmo(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts: list[str] = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        t = a.get_text(" ", strip=True)
        if any(k in href for k in ("/chapter", "/cap", "/chap")):
            texts.append(t)
    if not texts:
        return None
    return _max_from_texts(texts)

def parse_animebbg(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts: list[str] = []
    for a in soup.select("ul li a, .list-group a, a"):
        t = a.get_text(" ", strip=True)
        if CAP_WITH_CONTEXT.search(t) or re.search(r"#\s*\d", t):
            texts.append(t)
    if not texts:
        return None
    return _max_from_texts(texts)

def parse_bokugents(url: str) -> Optional[float]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    texts: list[str] = []
    for a in soup.select(".eplister a, .chapter a, .list a, a"):
        t = a.get_text(" ", strip=True)
        if CAP_WITH_CONTEXT.search(t) or re.search(r"#\s*\d", t, re.I):
            texts.append(t)
    if not texts:
        return None
    return _max_from_texts(texts)

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
    return parse_bokugents  # genérico

# ---------- Main ----------

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

            if s.last_chapter is None or new_ch > float(s.last_chapter):
                updates.append(f"**{s.name}** — {s.last_chapter or 0} -> **{new_ch}**\n<{s.url}>")
                s.last_chapter = new_ch
            else:
                print(f"[OK] Sin cambios: {s.name} (último {s.last_chapter})")

        except httpx.HTTPStatusError as e:
            warnings.append(f"No se pudo obtener {s.url}: {e}")
        except Exception as e:
            warnings.append(f"Error al parsear {s.url}: {e}")

    # Guardar
    try:
        save_series(series, LIB_PATH)
    except Exception as e:
        warnings.append(f"No pude guardar {LIB_PATH}: {e}")

    # Discord
    always_notify = (os.getenv("ALWAYS_NOTIFY") or "false").lower() == "true"
    if updates or warnings or always_notify:
        content = "Sin novedades." if not updates else "Novedades:"
        embeds: list[dict[str, Any]] = []
        if updates:
            embeds.append({"title": "Series con capítulos nuevos", "description": "\n\n".join(updates)[:4000]})
        if warnings:
            embeds.append({"title": "Avisos/errores", "description": "\n".join(f"• {w}" for w in warnings)[:4000]})
        send_discord(content, embeds)
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
