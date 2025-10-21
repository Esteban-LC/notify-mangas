\
#!/usr/bin/env python3
import os, json, sys, requests

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
if not WEBHOOK:
    print("[notify] Falta DISCORD_WEBHOOK_URL", file=sys.stderr)
    sys.exit(0)  # no-op

path = sys.argv[1] if len(sys.argv) > 1 else "updates.json"
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"[notify] Sin archivo de updates ({e})", file=sys.stderr)
    sys.exit(0)

updates = data.get("updates", [])
if not updates:
    print("[notify] No hay novedades", file=sys.stderr)
    sys.exit(0)

lines = ["**Actualizaciones de capítulos:**"]
for u in updates:
    lines.append(f"• **{u['name']}** ({u['site']}) → {u['previous']} ➜ **{u['latest']}**\n<{u['url']}>")

payload = {"content": "\n".join(lines)}
r = requests.post(WEBHOOK, json=payload, timeout=20)
print(f"[notify] Discord status {r.status_code}")
