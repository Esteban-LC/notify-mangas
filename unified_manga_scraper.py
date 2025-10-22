#!/usr/bin/env python3
import argparse
import sys
import re
import requests
import yaml
from bs4 import BeautifulSoup
from typing import Any, List, Dict, Tuple

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})

# ---------------- YAML ---------------- #

def load_library(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

def save_library(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

def normalize_library(library: Any) -> Tuple[List[Dict[str, Any]], bool, Any]:
    if isinstance(library, list):
        return library, True, library
    elif isinstance(library, dict):
        series = library.get("series", [])
        return series, False, library
    else:
        return [], False, {}

# ---------------- Parsers ---------------- #

CAP_RE = re.compile(r"(?:Cap[íi]tulo|Chapter)\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)

def get_html(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    return r.text

def extract_latest_chapter_generic_html(html: str) -> float:
    nums = [float(m.group(1)) for m in CAP_RE.finditer(html)]
    return max(nums) if nums else 0.0

def parser_manga_oni(url: str) -> float:
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")
    nums = []
    for h in soup.select("h3"):
        m = CAP_RE.search(h.get_text(" ", strip=True))
        if m:
            nums.append(float(m.group(1)))
    return max(nums) if nums else extract_latest_chapter_generic_html(html)

def parser_mangasnosekai(url: str) -> float:
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")
    nums = [float(m.group(1)) for m in CAP_RE.finditer(str(soup))]
    return max(nums) if nums else 0.0

def parser_zonatmo(url: str) -> float:
    html = get_html(url)
    return extract_latest_chapter_generic_html(html)

def parser_generic(url: str) -> float:
    html = get_html(url)
    return extract_latest_chapter_generic_html(html)

def pick_parser(site: str):
    s = site.lower()
    if "manga-oni" in s:
        return parser_manga_oni
    if "mangasnosekai" in s:
        return parser_mangasnosekai
    if "zonatmo" in s or "tmo" in s:
        return parser_zonatmo
    return parser_generic

# ---------------- Notificaciones ---------------- #

def notify_discord(webhook_url: str, title: str, content: str):
    if not webhook_url:
        return
    payload = {
        "username": "notify-mangas",
        "content": f"**{title}**\n{content}",
    }
    try:
        requests.post(webhook_url, json=payload, timeout=15)
    except Exception as e:
        print(f"[WARN] No se pudo notificar a Discord: {e}", file=sys.stderr)

# ---------------- Main ---------------- #

def main():
    ap = argparse.ArgumentParser(description="Manga auto checker")
    ap.add_argument("--library", default="manga_library.yml")
    ap.add_argument("--discord", default="")
    ap.add_argument("--bootstrap", action="store_true",
                    help="Inicializa el YAML sin notificar (modo auto)")
    args = ap.parse_args()

    library = load_library(args.library)
    series, root_is_list, original = normalize_library(library)
    updated = False
    notify_msgs = []

    for s in series:
        name = s.get("name", "").strip()
        site = s.get("site", "").strip()
        url = s.get("url", "").strip()
        raw_last = s.get("last_chapter", None)
        parser = pick_parser(site or url)

        bootstrap = args.bootstrap or raw_last in (None, "auto", "", 0)

        try:
            latest = parser(url)
        except Exception as e:
            print(f"[ERROR] {name}: {e}")
            continue

        if bootstrap:
            s["last_chapter"] = latest
            updated = True
            print(f"[INIT] {name} → {latest}")
            continue

        try:
            last = float(raw_last)
        except (TypeError, ValueError):
            last = 0.0

        if latest > last:
            s["last_chapter"] = latest
            updated = True
            msg = f"• **{name}** ({site}) — nuevo: **{latest}** (antes: {last})\n{url}"
            notify_msgs.append(msg)
            print(f"[UPDATE] {msg}")

    if updated:
        if root_is_list:
            save_library(args.library, series)
        else:
            original["series"] = series
            save_library(args.library, original)

        if not args.bootstrap and notify_msgs:
            notify_discord(args.discord, "¡Nuevos capítulos detectados!", "\n\n".join(notify_msgs))
        print("✅ Cambios guardados.")
    else:
        print("Sin novedades.")

if __name__ == "__main__":
    main()
