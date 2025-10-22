# unified_manga_scraper.py
# -*- coding: utf-8 -*-

import os, re, json, time, random
from urllib.parse import urlparse
from typing import Tuple, Optional, Dict, Any

import yaml
from bs4 import BeautifulSoup

import requests
try:
    import cloudscraper
except ImportError:
    cloudscraper = None


# =========================
# Config
# =========================

LIB_FILE = "manga_library.yml"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

# Sitios que suelen requerir bypass
FORCE_CLOUDSCRAPER = {"m440.in", "mangasnosekai.com", "zonatmo.com", "animebbg.net"}

# User-Agents rotativos
DESKTOP_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
]

RNG = random.Random()


# =========================
# Utilidades HTTP
# =========================

def _base_headers() -> Dict[str, str]:
    return {
        "User-Agent": RNG.choice(DESKTOP_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }

def _load_extra_cookies() -> Dict[str, Dict[str, str]]:
    """
    EXTRA_COOKIES_JSON (opcional) con formato:
    {
      "m440.in": {"cf_clearance":"..."},
      "mangasnosekai.com": {"cf_clearance":"..."},
      "zonatmo.com": {"cf_clearance":"..."},
      "animebbg.net": {"xf_session":"..."}
    }
    """
    raw = os.environ.get("EXTRA_COOKIES_JSON", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        norm = {}
        for k, v in data.items():
            host = urlparse(k).netloc or k
            host = host.replace("https://", "").replace("http://", "").strip().strip("/")
            norm[host] = v
        return norm
    except Exception:
        return {}

EXTRA_COOKIES = _load_extra_cookies()

def _apply_cookies(session: requests.Session, netloc: str) -> None:
    cookies = EXTRA_COOKIES.get(netloc)
    if cookies:
        for ck, cv in cookies.items():
            session.cookies.set(ck, cv, domain="." + netloc)

def _apply_proxy(session: requests.Session) -> None:
    proxy_url = os.environ.get("PROXY_URL", "").strip()
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})

def _make_requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_base_headers())
    _apply_proxy(s)
    return s

def _make_cloudscraper_session():
    if not cloudscraper:
        return None
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    s.headers.update(_base_headers())
    _apply_proxy(s)
    return s

def fetch_html(url: str, timeout: int = 30, max_tries: int = 3, force_cloudscraper: bool = False) -> Tuple[int, str]:
    """
    Devuelve (status_code, html) o lanza RuntimeError.
    - Cookies por dominio (EXTRA_COOKIES_JSON)
    - Proxy opcional (PROXY_URL)
    - Fallback a cloudscraper si 403/503 o dominio en FORCE_CLOUDSCRAPER
    """
    netloc = urlparse(url).netloc
    use_cloud = force_cloudscraper or (netloc in FORCE_CLOUDSCRAPER)

    sess = _make_cloudscraper_session() if use_cloud else _make_requests_session()
    if not sess:
        # Si no tenemos cloudscraper instalado pero se forzó, caemos a requests
        sess = _make_requests_session()
    _apply_cookies(sess, netloc)

    r = None
    for attempt in range(1, max_tries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            if r.status_code in (200, 201):
                return r.status_code, r.text

            # Si 403/503 con requests, intenta cloudscraper
            if r.status_code in (403, 503) and not use_cloud and cloudscraper:
                use_cloud = True
                sess = _make_cloudscraper_session()
                if sess:
                    _apply_cookies(sess, netloc)
                    rr = sess.get(url, timeout=timeout)
                    if rr.status_code in (200, 201):
                        return rr.status_code, rr.text
                    r = rr

            # backoff
            time.sleep(1.5 * attempt + RNG.uniform(0, 1.2))
        except requests.RequestException:
            time.sleep(1.5 * attempt + RNG.uniform(0, 1.2))

    code = getattr(r, "status_code", "sin respuesta")
    raise RuntimeError(f"No se pudo obtener {url}: {code}")


# =========================
# Parse helpers
# =========================

CAP_PATTERNS = [
    r"cap[ií]tulo\s*([0-9]+(?:[.,-][0-9]+)?)",
    r"chapter\s*([0-9]+(?:[.,-][0-9]+)?)",
    r"\bch\s*([0-9]+(?:[.,-][0-9]+)?)\b",
]

def normalize_chapter(text: str) -> Optional[float]:
    """Extrae número de capítulo desde texto, devuelve float (p.ej. '7-20' => 7.20)."""
    if not text:
        return None
    t = text.lower().strip()
    # Primero intenta patrones "Capítulo X", "Chapter X"
    for pat in CAP_PATTERNS:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            val = m.group(1)
            val = val.replace(",", ".").replace("-", ".")
            try:
                return float(val)
            except ValueError:
                pass
    # Si no, intenta capturar el primer número razonable
    m2 = re.search(r"([0-9]+(?:[.,][0-9]+)?)", t)
    if m2:
        val = m2.group(1).replace(",", ".")
        try:
            return float(val)
        except ValueError:
            return None
    return None


# =========================
# Parsers por sitio
# =========================

def parse_madara_latest(html: str) -> Optional[float]:
    """
    Madara (Bokugents, MangasNoSekai, m440.in).
    Suele listar <li class="wp-manga-chapter"> <a>Capítulo X</a>
    Tomamos el máximo por seguridad (a veces orden asc/desc cambia).
    """
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("li.wp-manga-chapter a")
    caps = []
    for a in links:
        txt = " ".join(a.get_text(strip=True).split())
        ch = normalize_chapter(txt)
        if ch is not None:
            caps.append(ch)
    return max(caps) if caps else None

def parse_zonatmo_latest(html: str) -> Optional[float]:
    """
    Zonatmo: busca todos los textos 'Capítulo X' en los headers de ítems.
    """
    soup = BeautifulSoup(html, "html.parser")
    caps = []
    # h4 Capítulo X...
    for h4 in soup.select("#chapters li.list-group-item h4 a.btn-collapse"):
        txt = " ".join(h4.get_text(strip=True).split())
        ch = normalize_chapter(txt)
        if ch is not None:
            caps.append(ch)
    # fallback: cualquier 'a' con "Capítulo"
    if not caps:
        for a in soup.find_all("a"):
            t = a.get_text(strip=True)
            if "Cap" in t or "cap" in t:
                ch = normalize_chapter(t)
                if ch is not None:
                    caps.append(ch)
    return max(caps) if caps else None

def parse_animebbg_latest(html: str) -> Optional[float]:
    """
    AnimeBBG (XenForo): cards con 'structItem--resourceAlbum' y título 'Capítulo X'.
    Tomamos el máximo encontrado en la página.
    """
    soup = BeautifulSoup(html, "html.parser")
    caps = []
    for title in soup.select(".structItem--resourceAlbum .structItem-title"):
        txt = " ".join(title.get_text(" ", strip=True).split())
        ch = normalize_chapter(txt)
        if ch is not None:
            caps.append(ch)
    # fallback global
    if not caps:
        for a in soup.find_all("a"):
            ch = normalize_chapter(a.get_text(strip=True))
            if ch is not None:
                caps.append(ch)
    return max(caps) if caps else None


def get_latest_chapter(url: str, site_hint: Optional[str], force_cloud: bool) -> Optional[float]:
    """
    Detecta parser por dominio (o hint) y devuelve último capítulo (float).
    """
    netloc = urlparse(url).netloc
    host = netloc.lower()
    hint = (site_hint or "").lower().strip()

    # Fetch
    status, html = fetch_html(url, force_cloudscraper=force_cloud or (host in FORCE_CLOUDSCRAPER))

    # Parse según dominio/hint
    if any(s in host for s in ["bokugents.com"]) or "bokugents" in hint:
        return parse_madara_latest(html)
    if any(s in host for s in ["mangasnosekai.com"]) or "mangasnosekai" in hint:
        return parse_madara_latest(html)
    if any(s in host for s in ["m440.in"]) or "m440" in hint:
        return parse_madara_latest(html)
    if any(s in host for s in ["zonatmo.com"]) or "zonatmo" in hint:
        return parse_zonatmo_latest(html)
    if any(s in host for s in ["animebbg.net"]) or "animebbg" in hint:
        return parse_animebbg_latest(html)

    # Fallback genérico Madara (muchos clones)
    return parse_madara_latest(html)


# =========================
# YAML I/O
# =========================

def load_library() -> Dict[str, Any]:
    """
    Estructura esperada:
    series:
      - name: ...
        site: ...
        url: ...
        last_chapter: 0 | 0.0 | null
        force_cloudscraper: true|false (opcional)
    """
    if not os.path.exists(LIB_FILE):
        return {"series": []}
    with open(LIB_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
        if "series" not in data or not isinstance(data["series"], list):
            # soporte para formato viejo (lista a tope)
            if isinstance(data, list):
                return {"series": data}
            return {"series": []}
        return data

def save_library(data: Dict[str, Any]) -> None:
    with open(LIB_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


# =========================
# Discord
# =========================

def notify_discord_new(name: str, url: str, old: Optional[float], new: float):
    if not DISCORD_WEBHOOK_URL:
        return
    content = f"**[NUEVO]** {name} — {old if old is not None else 0} -> **{new}**\n{url}"
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=15)
    except Exception:
        pass

def notify_discord_info(msg: str):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=15)
    except Exception:
        pass


# =========================
# Main
# =========================

def main():
    lib = load_library()
    series = lib.get("series", [])
    if not series:
        print("No hay series en manga_library.yml")
        return

    something_new = False
    for idx, s in enumerate(series, start=1):
        name = s.get("name", "¿Sin nombre?")
        url = s.get("url")
        site = s.get("site", "")
        last = s.get("last_chapter", None)
        force = bool(s.get("force_cloudscraper", False))

        if not url:
            print(f"[WARN] Serie sin URL: {name}")
            continue

        # Espera aleatoria entre 4–9s para no gatillar WAF
        if idx > 1:
            time.sleep(RNG.uniform(4.0, 9.0))

        try:
            latest = get_latest_chapter(url, site, force)
            if latest is None:
                print(f"[WARN] No pude encontrar capítulo en: {url} — «{name}»")
                continue

            # Coerce last a float o None
            last_f = None
            if last is not None:
                try:
                    last_f = float(last)
                except Exception:
                    last_f = None

            if (last_f is None) or (latest > last_f):
                # Nuevo capítulo
                print(f"[NUEVO] {name} — {last_f if last_f is not None else 0} -> {latest}")
                s["last_chapter"] = latest
                notify_discord_new(name, url, last_f, latest)
                something_new = True
        except RuntimeError as e:
            # Errores esperados de fetch (403, 503, etc.)
            msg = str(e)
            print(f"Error:  {msg}")
            # Log a Discord sólo si quieres ver el fallo
            # notify_discord_info(f"Error con **{name}**: {msg}")
        except Exception as e:
            print(f"Error desconocido en {name}: {e}")

    # Guardar si hubo cambios
    if something_new:
        save_library(lib)
    else:
        print("Sin novedades.")


if __name__ == "__main__":
    main()
