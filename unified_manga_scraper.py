\
#!/usr/bin/env python3
import argparse, json, os, re, sys, time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import yaml
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; MangaChecker/1.0; +https://example.local)"

@dataclass
class Series:
    name: str
    site: str
    url: str
    last_seen: Optional[float] = None  # last chapter number

def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    return r.text

def extract_number(text: str) -> Optional[float]:
    # Find first number like 166 or 163.5
    m = re.search(r'(\d+(?:\.\d+)?)', text.replace(',', '.'))
    return float(m.group(1)) if m else None

def parse_manga_oni(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "lxml")
    # Titles like "Capítulo 166"
    nums = []
    for h3 in soup.select("#c_list h3.entry-title-h2"):
        n = extract_number(h3.get_text(" ", strip=True))
        if n is not None:
            nums.append(n)
    return max(nums) if nums else None

def parse_mangasnosekai(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "lxml")
    nums = []
    # Divs with text "Capítulo 93"
    for div in soup.select(".container-capitulos .contenedor-capitulo-miniatura .text-sm"):
        t = div.get_text(" ", strip=True)
        if "Capítulo" in t:
            n = extract_number(t)
            if n is not None:
                nums.append(n)
    # Fallback: links /capitulo-93/
    for a in soup.select("a[href*='/capitulo-']"):
        n = extract_number(a.get("href",""))
        if n is not None:
            nums.append(n)
    return max(nums) if nums else None

def parse_m440(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "lxml")
    nums = []
    # Look for list items with chapter numbers like "#100" inside
    for li in soup.select("li"):
        text = li.get_text(" ", strip=True)
        if "Capítulo" in text or "#" in text:
            n = extract_number(text)
            if n is not None:
                nums.append(n)
    # Fallback: links with /manga/.../<number-...>
    for a in soup.select("a[href*='/manga/']"):
        href = a.get("href","")
        n = extract_number(href.split("/")[-1])
        if n is not None:
            nums.append(n)
    return max(nums) if nums else None

def parse_zonatmo(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "lxml")
    nums = []
    # Collapsible items like "Capítulo 1.00 ..."
    for li in soup.select("ul.list-group li.list-group-item.upload-link h4 a.btn-collapse"):
        t = li.get_text(" ", strip=True)
        if "Capítulo" in t:
            n = extract_number(t)
            if n is not None:
                nums.append(n)
    # Fallback: direct chapter view links with view_uploads/<id> but no number there
    # Try also anchors containing 'Capítulo'
    for a in soup.find_all("a"):
        t = a.get_text(" ", strip=True)
        if "Capítulo" in t:
            n = extract_number(t)
            if n is not None:
                nums.append(n)
    return max(nums) if nums else None

PARSERS = {
    "manga-oni.com": parse_manga_oni,
    "mangasnosekai.com": parse_mangasnosekai,
    "m440.in": parse_m440,
    "zonatmo.com": parse_zonatmo,
}

def load_library(path: str) -> List[Series]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    lib = []
    for item in data:
        lib.append(Series(
            name=item["name"],
            site=item["site"],
            url=item["url"],
            last_seen=float(item.get("last_seen")) if item.get("last_seen") is not None else None
        ))
    return lib

def save_library(path: str, items: List[Series]) -> None:
    data = []
    for s in items:
        d = {"name": s.name, "site": s.site, "url": s.url}
        if s.last_seen is not None:
            d["last_seen"] = s.last_seen
        data.append(d)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", default="manga_library.yaml")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--save", action="store_true", help="Guarda el último capítulo detectado como baseline")
    ap.add_argument("--report", default=None, help="Escribe un JSON con novedades")
    args = ap.parse_args()

    lib = load_library(args.library)
    updates: List[Dict[str, Any]] = []

    for s in lib:
        parser = PARSERS.get(s.site)
        if not parser:
            print(f"[WARN] No tengo parser para {s.site} en '{s.name}'", file=sys.stderr)
            continue
        try:
            html = fetch(s.url)
            latest = parser(html)
        except Exception as e:
            print(f"[ERROR] {s.name} ({s.site}): {e}", file=sys.stderr)
            continue

        if latest is None:
            print(f"[WARN] No pude detectar capítulo para {s.name}", file=sys.stderr)
            continue

        changed = (s.last_seen is None) or (latest > s.last_seen)
        print(f"- {s.name} @ {s.site}: último={latest} (guardado={s.last_seen}) {'[NUEVO]' if changed and s.last_seen is not None else ''}")
        if changed and s.last_seen is not None:
            updates.append({
                "name": s.name,
                "site": s.site,
                "url": s.url,
                "latest": latest,
                "previous": s.last_seen
            })

        if args.save:
            s.last_seen = latest

    if args.save:
        save_library(args.library, lib)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump({"updates": updates, "ts": int(time.time())}, f, ensure_ascii=False, indent=2)

    # exit code: 0 if ok; 2 if updates to signal GH Actions step condition
    if updates:
        sys.exit(2)

if __name__ == "__main__":
    main()
