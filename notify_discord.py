#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import time
from typing import List

import requests

MAX_DISCORD = 2000
CHUNK_SIZE = 1900  # margen para cabeceras/formatos

def _chunks(s: str, n: int = CHUNK_SIZE) -> List[str]:
    return [s[i:i+n] for i in range(0, len(s), n)]

def send_lines(webhook_url: str, lines: List[str], username: str = "notify-mangas", avatar_url: str = "") -> None:
    text = "\n".join(lines).strip()
    if not text:
        return

    blocks = _chunks(text, CHUNK_SIZE)
    for i, block in enumerate(blocks, 1):
        payload = {
            "content": block,
            "username": username,
        }
        if avatar_url:
            payload["avatar_url"] = avatar_url

        resp = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Discord {resp.status_code}: {resp.text[:300]}")

        # evitar rate limit
        if i < len(blocks):
            time.sleep(1.2)
