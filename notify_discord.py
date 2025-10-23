import os
import time
from typing import Iterable, List, Optional
import requests


def _get_webhook_url() -> Optional[str]:
    # acepta cualquiera de los dos nombres de secret
    return os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK")


def _chunks(s: str, n: int) -> List[str]:
    if len(s) <= n:
        return [s]
    out, cur, total = [], [], 0
    for line in s.splitlines():
        ln = len(line) + 1  # \n
        if total + ln > n and cur:
            out.append("\n".join(cur))
            cur, total = [line], ln
        else:
            cur.append(line)
            total += ln
    if cur:
        out.append("\n".join(cur))
    return out


def _post(webhook: str, content: str) -> None:
    payload = {"content": content}
    last = None
    for i in range(3):
        try:
            r = requests.post(webhook, json=payload, timeout=15)
            last = r
            if r.ok:
                return
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    print(f"[WARN] Falló enviar a Discord: {last}")


def _format_list(title: str, items: Iterable[str]) -> List[str]:
    body = "\n".join(f"• {i}" for i in items) if items else "—"
    # 2000 es el límite, dejamos margen por el formato
    parts = _chunks(body, 1900)
    return [f"**{title}:**\n```{p}```" for p in parts]


def send_discord(
    updates: Optional[Iterable[str]] = None,
    warnings: Optional[Iterable[str]] = None,
    oks: Optional[Iterable[str]] = None,
) -> None:
    """
    Envía el resumen a Discord.
    - updates: novedades (cambios detectados)
    - warnings: avisos/errores
    - oks: revisadas / sin cambios ([OK] ...)
    """
    webhook = _get_webhook_url()
    if not webhook:
        print("[WARN] DISCORD_WEBHOOK no configurado; no se enviará notificación.")
        return

    updates = list(updates or [])
    warnings = list(warnings or [])
    oks = list(oks or [])

    if not updates:
        _post(webhook, "**Sin novedades.**")

    if updates:
        for block in _format_list("Novedades", updates):
            _post(webhook, block)

    if oks:
        for block in _format_list("Revisadas / sin cambios", oks):
            _post(webhook, block)

    if warnings:
        for block in _format_list("Avisos/errores", warnings):
            _post(webhook, block)
