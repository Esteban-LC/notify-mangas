#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import math
import argparse
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

import yaml
from bs4 import BeautifulSoup

# Usamos cloudscraper para evitar 403/Cloudflare
import cloudscraper
from requests.exceptions import RequestException

STATE_FILE = Path("manga_state.json")
LIB_FILE = Path("manga_library.yml")
STATE_CHANGED_FLAG = Path(".state_changed")

# ---------- Utilidades ----------

def to_float_cap(raw: str) -> float:
    """
    Convierte 'Capítulo 166', 'Capítulo 163.5', '1.00', etc. a float.
    Si no encuentra número devuelve -inf para que no bloquee notificaciones.
    """
    m = re.search(r'(\d+(?:[.,]\d+)?)', raw.replace(",", "."))
    if not m:
        return float("-inf")
    try:
        return float(m.group(1))
    except ValueError:
        return float("-inf")


def create_scraper() -> cloudscraper.CloudScraper:
    # Cabeceras realistas
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
    s.headers.update(headers)
    return s


def fetch_html(url: str, timeout: int = 30, retries: int = 3, sleep_seconds: float = 1.2) -> str:
    """
    Descarga HTML evitando Cloudflare (cloudscraper) + reintentos.
    """
    scraper = create_scraper()
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = scraper.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except RequestException as exc:
            last_exc = exc
            # Espera escalonada
            time.sleep(sleep_seconds * attempt)
    raise RuntimeError(f"No pude descargar {url}: {last_exc}")


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

# ---------- Parsers de sitios ----------

def parse_manga_oni(url: str) -> Tuple[str, str]:
    """
    https://manga-oni.com/lector/<slug>/
    En la página de la serie, dentro de #c_list los <a> son capítulos (el primero es el más reciente).
    Devuelve: (capitulo_label, cap_url)
    """
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    c_list = soup.select_one("#c_list")
    if not c_list:
        # fallback: primer enlace con 'Capítulo'
        first = soup.find("h3", class_="entry-title-h2")
        if not first:
            raise RuntimeError("No encontré lista de capítulos en manga-oni")
        a = first.find_parent("a")
        cap_label = first.get_text(strip=True)
        cap_url = a["href"] if a and a.has_attr("href") else url
        return (cap_label, cap_url)

    a = c_list.find("a")
    if not a:
        raise RuntimeError("No hay capítulos en manga-oni")

    h3 = a.select_one("h3.entry-title-h2")
    cap_label = h3.get_text(" ", strip=True) if h3 else a.get_text(" ", strip=True)
    cap_url = a["href"]
    return (cap_label, cap_url)


def parse_mangasnosekai(url: str) -> Tuple[str, str]:
    """
    https://mangasnosekai.com/manga/<slug>/
    En la ficha: .container-capitulos .contenedor-capitulo-miniatura -> primero es el más reciente.
    """
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    cont = soup.select_one(".container-capitulos")
    if not cont:
        raise RuntimeError("No encontré la lista de capítulos en MangasNoSekai")

    card = cont.select_one(".contenedor-capitulo-miniatura")
    if not card:
        raise RuntimeError("No hay capítulos en MangasNoSekai")

    # Texto "Capítulo 93"
    txt = card.get_text(" ", strip=True)
    cap_label = re.search(r"Cap[íi]tulo\s+\d+(?:[.,]\d+)?", txt, flags=re.I)
    cap_label = cap_label.group(0) if cap_label else txt

    # Link del capítulo: el primer <a> que apunte a /capitulo/
    a = card.find("a", href=re.compile(r"/capitulo/\d+/"))
    cap_url = a["href"] if a and a.has_attr("href") else url
    # Absolutiza si hace falta
    if cap_url.startswith("/"):
        cap_url = "https://mangasnosekai.com" + cap_url

    return (cap_label, cap_url)


def parse_zonatmo(url: str) -> Tuple[str, str]:
    """
    https://zonatmo.com/library/manhwa/...  (o manhua/manga)
    En la ficha: dentro de .card.chapters -> li.upload-link (colapsable del capítulo)
    Tomamos el primero como el más reciente.
    """
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    card = soup.select_one(".card.chapters")
    if not card:
        raise RuntimeError("No encontré el bloque de capítulos en ZonaTMO")

    li = card.select_one("li.upload-link")
    if not li:
        raise RuntimeError("No hay capítulos en ZonaTMO")

    # Título tipo: "Capítulo 1.00  Bebe en la lluvia 1"
    title_h4 = li.find("h4")
    cap_text = title_h4.get_text(" ", strip=True) if title_h4 else li.get_text(" ", strip=True)
    # Normalizamos a "Capítulo X.YY"
    m = re.search(r"(Cap[íi]tulo\s+\d+(?:[.,]\d+)?)", cap_text, flags=re.I)
    cap_label = m.group(1) if m else cap_text

    # Link al lector está dentro del sub-<li> con botón de "play"
    play = li.select_one('a.btn.btn-default[href*="/view_uploads/"]')
    cap_url = play["href"] if play and play.has_attr("href") else url
    if cap_url.startswith("/"):
        cap_url = "https://zonatmo.com" + cap_url

    return (cap_label, cap_url)


# Mapa de dominio -> parser
PARSERS = {
    "manga-oni.com": parse_manga_oni,
    "mangasnosekai.com": parse_mangasnosekai,
    "zonatmo.com": parse_zonatmo,
}

def pick_parser(series_url: str):
    for host, fn in PARSERS.items():
        if host in series_url:
            return fn
    return None


# ---------- Notificación opcional (Discord) ----------

def notify_discord(webhook: str, title: str, description: str, url: str) -> None:
    """
    Se apoya en notify_discord.py para construir un embed bonito.
    """
    import subprocess, sys, json
    payload = {
        "title": title,
        "description": description,
        "url": url,
    }
    subprocess.check_call(
        [sys.executable, "notify_discord.py", "--webhook", webhook, "--payload", json.dumps(payload)],
    )


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Revisa últimas publicaciones de mangas/manhwas.")
    parser.add_argument("--discord", help="Webhook de Discord (opcional)", default=os.getenv("DISCORD_WEBHOOK_URL", ""))
    args = parser.parse_args()

    if not LIB_FILE.exists():
        raise SystemExit(f"No encuentro {LIB_FILE} (debe contener tu lista).")

    library = load_yaml(LIB_FILE)
    series = library.get("series", [])
    if not isinstance(series, list) or not series:
        raise SystemExit("manga_library.yml no tiene 'series' válidas.")

    state = load_json(STATE_FILE)

    updates = []
    for item in series:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            print(f"[WARN] Entrada inválida (name/url): {item}")
            continue

        parser_fn = pick_parser(url)
        if not parser_fn:
            print(f"[WARN] No tengo parser para {url} en ‘{name}’")
            continue

        try:
            cap_label, cap_url = parser_fn(url)
        except Exception as e:
            print(f"Error: {name} ({url}): {e}")
            continue

        # Normalizamos número
        current_num = to_float_cap(cap_label)

        prev = state.get(url, {})
        prev_num = float(prev.get("chapter_num", float("-inf")))
        prev_label = prev.get("chapter_label", "")

        if current_num > prev_num:
            # Nuevo capítulo
            updates.append((name, cap_label, cap_url))
            state[url] = {
                "name": name,
                "chapter_label": cap_label,
                "chapter_num": current_num,
                "chapter_url": cap_url,
                "checked_at": int(time.time()),
            }
        else:
            # Sin novedad -> actualiza timestamp
            state.setdefault(url, {})
            state[url].update({
                "name": name,
                "chapter_label": prev_label or cap_label,
                "chapter_num": prev_num if math.isfinite(prev_num) else current_num,
                "chapter_url": prev.get("chapter_url", cap_url),
                "checked_at": int(time.time()),
            })

    # Guardamos estado y marcamos si hubo cambios
    prev_state = load_json(STATE_FILE)
    save_json(STATE_FILE, state)

    changed = json.dumps(prev_state, sort_keys=True) != json.dumps(state, sort_keys=True)
    if changed:
        STATE_CHANGED_FLAG.write_text("changed", encoding="utf-8")

    # Notificaciones
    if updates:
        print("=== NUEVOS CAPÍTULOS ===")
        for name, cap_label, cap_url in updates:
            print(f"- {name}: {cap_label} -> {cap_url}")
            if args.discord:
                try:
                    notify_discord(
                        args.discord,
                        title=f"🔔 {name} — {cap_label}",
                        description=f"Nuevo capítulo encontrado.",
                        url=cap_url,
                    )
                except Exception as e:
                    print(f"[WARN] No pude notificar a Discord: {e}")
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    main()
