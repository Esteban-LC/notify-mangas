import os
import re
import json
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
import yaml

from notify_discord import send_discord


LIB_PATH = "manga_library.yml"


# ---------- utilidades de red ----------

def build_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "close",
    })

    proxy = os.getenv("PROXY_URL")
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})

    # cookies opcionales por dominio (json: {"m440.in": "cookie1=...; x=y"})
    extra = os.getenv("EXTRA_COOKIES_JSON")
    try:
        cookie_map = json.loads(extra) if extra else {}
    except Exception:
        cookie_map = {}
    sess._extra_cookies_map = cookie_map  # type: ignore[attr-defined]
    return sess


def fetch(session: requests.Session, url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    retorna (html, error). En error -> None/str
    """
    try:
        host = urlparse(url).hostname or ""
        # inyecta cookies por host si hay coincidencia de sufijo
        cookie_map = getattr(session, "_extra_cookies_map", {}) or {}
        cookie_value = None
        for dom, val in cookie_map.items():
            if host.endswith(dom):
                cookie_value = val
                break
        headers = {}
        if cookie_value:
            headers["Cookie"] = cookie_value

        r = session.get(url, headers=headers, timeout=25)
        if r.status_code != 200:
            return None, f"{r.status_code} Forbidden en {url}"
        return r.text, None
    except Exception as e:
        return None, str(e)


# ---------- parsing de capítulo ----------

_NUMBER_PATTERNS = [
    # data-number="166_5"  /  data-number="168"
    re.compile(r'data-number="(\d+(?:[_\.]\d+)?)"', re.I),
    # href ... /166_5-xxxxx
    re.compile(r'/(\d+(?:_\d+)?)\-[a-z0-9]+["\']', re.I),
    # #168 ⠇  /  #159.5 ⠇
    re.compile(r'#\s*(\d+(?:\.\d+)?)\s*[<\s⠇]', re.I),
    # Capítulo 168  / Capitulo 168.5
    re.compile(r'cap[ií]tulo\s*(\d+(?:\.\d+)?)', re.I),
    # number": "168"  (por si hay JSON embebido)
    re.compile(r'"number"\s*:\s*"(\d+(?:\.\d+)?)"', re.I),
]

def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace("_", "."))
    except Exception:
        return None

def extract_latest_from_html(html: str) -> Optional[float]:
    candidates: List[float] = []
    for pat in _NUMBER_PATTERNS:
        for m in pat.findall(html):
            f = _to_float(m if isinstance(m, str) else m[0])
            if f is not None:
                candidates.append(f)
    if not candidates:
        return None
    return max(candidates)

# ---------- manejo YAML ----------

def load_series(path: str = LIB_PATH) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("series") or [])

def save_series(items: List[Dict[str, Any]], path: str = LIB_PATH) -> None:
    data = {"series": items}
    txt = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=88)
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)

# ---------- main ----------

def main():
    items = load_series()

    sess = build_session()

    changes_for_discord: List[str] = []
    warnings_for_discord: List[str] = []
    oks_for_discord: List[str] = []

    for s in items:
        name = s.get("name") or ""
        url = s.get("url") or ""
        last = s.get("last_chapter")
        last_f = _to_float(str(last)) if last is not None else None

        html, err = fetch(sess, url)
        if err:
            msg = f"No se pudo obtener {url}: {err}"
            print(f"[WARN] {msg}")
            warnings_for_discord.append(msg)
            # sin HTML no comparamos; seguimos
            continue

        latest = extract_latest_from_html(html)
        if latest is None:
            msg = f"No pude encontrar capítulo en: {url} — «{name}»"
            print(f"[WARN] {msg}")
            warnings_for_discord.append(msg)
            continue

        # comparar y decidir
        if last_f is None or latest > last_f:
            old = last_f if last_f is not None else "?"
            s["last_chapter"] = latest
            msg = f"[NUEVO] {name} — {old} -> {latest}"
            print(msg)
            changes_for_discord.append(msg)
        else:
            msg = f"[OK] Sin cambios: {name} (último {last_f})"
            print(msg)
            oks_for_discord.append(msg)

    # guardar YAML (solo si hay cambios ya lo reflejará git)
    save_series(items)

    # enviar a Discord (incluye “Sin novedades.” si no hubo cambios)
    send_discord(
        updates=changes_for_discord,
        warnings=warnings_for_discord,
        oks=oks_for_discord,
    )

if __name__ == "__main__":
    main()
