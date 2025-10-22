#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import argparse
import requests

def send_embed(webhook: str, title: str, description: str, url: str):
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "url": url,
                "color": 0x2ecc71,
            }
        ]
    }
    r = requests.post(webhook, json=payload, timeout=30)
    r.raise_for_status()
    return r.text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--webhook", required=False, default=os.getenv("DISCORD_WEBHOOK_URL", ""))
    ap.add_argument("--payload", required=True, help="JSON con title/description/url")
    args = ap.parse_args()

    if not args.webhook:
        print("[INFO] No webhook de Discord: no se enviará nada.")
        return

    data = json.loads(args.payload)
    title = data.get("title", "Notificación")
    description = data.get("description", "")
    url = data.get("url", "")

    send_embed(args.webhook, title, description, url)

if __name__ == "__main__":
    main()
