import yaml
from pathlib import Path

LIB = Path("manga_library.yml")

def normalize(series):
    # orden estable por nombre y normaliza tipos numéricos
    def to_float(x):
        try:
            if x is None:
                return None
            return float(str(x).replace("_", "."))
        except Exception:
            return x

    out = []
    for s in series:
        s = dict(s)
        s["last_chapter"] = to_float(s.get("last_chapter"))
        out.append({
            "name": s.get("name"),
            "site": s.get("site"),
            "url": s.get("url"),
            "last_chapter": s.get("last_chapter"),
        })
    out.sort(key=lambda x: (x.get("name") or "").lower())
    return out

def main():
    if not LIB.exists():
        print("manga_library.yml no existe; nada que formatear.")
        return
    data = yaml.safe_load(LIB.read_text(encoding="utf-8")) or {}
    series = data.get("series") or []
    fixed = {"series": normalize(series)}
    # volcado estable
    text = yaml.safe_dump(
        fixed, sort_keys=False, allow_unicode=True, width=88
    )
    LIB.write_text(text, encoding="utf-8")
    print("manga_library.yml normalizado.")

if __name__ == "__main__":
    main()
