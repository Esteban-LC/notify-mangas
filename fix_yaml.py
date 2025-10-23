#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import yaml

PATH = "manga_library.yml"

def main():
    with open(PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    series = data.get("series", [])
    out = []
    for it in series:
        it["name"] = str(it.get("name", "")).strip()
        it["site"] = str(it.get("site", "")).strip()
        it["url"] = str(it.get("url", "")).strip()
        lc = it.get("last_chapter", None)
        if lc in (None, "", "null"):
            it["last_chapter"] = None
        else:
            try:
                it["last_chapter"] = float(str(lc).replace(",", "."))
            except Exception:
                it["last_chapter"] = None
        out.append(it)

    with open(PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump({"series": out}, f, allow_unicode=True, sort_keys=False)

if __name__ == "__main__":
    main()
