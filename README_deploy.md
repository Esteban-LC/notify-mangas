# Despliegue gratis con GitHub Actions (chequeos programados)

Este proyecto corre **sin servidor** usando *GitHub Actions* cada 30 minutos y te notifica a Discord si encuentra capítulos nuevos.

## Archivos incluidos
- `unified_manga_scraper.py` — scraper unificado para `manga-oni.com`, `mangasnosekai.com`, `m440.in`, `zonatmo.com`
- `manga_library.yaml` — tu lista de obras (solo `name`, `site`, `url`)
- `notify_discord.py` — envía novedades a un webhook de Discord
- `.github/workflows/manga-check.yml` — workflow programado
- `requirements.txt`

## Pasos
1. Crea un repositorio (público) y sube estos archivos a la raíz manteniendo la carpeta `.github/workflows/`.
2. En tu servidor de Discord, crea un **Webhook** y copia la URL.
3. En GitHub → *Settings* → *Secrets and variables* → *Actions* → **New repository secret**:
   - Nombre: `DISCORD_WEBHOOK_URL`
   - Valor: pega la URL del webhook
4. Edita `manga_library.yaml` y agrega tus series con `name`, `site` y `url`.
5. *Opcional:* Ejecuta el workflow manualmente desde la pestaña **Actions** con **Run workflow** para que haga un primer chequeo y actualice baseline cuando lo dispares manualmente (el workflow ya está configurado para hacer commit de `last_seen` solo cuando es manual).

> **Nota:** Los cron jobs en GitHub Actions no están garantizados al minuto exacto y pueden “derivar”, pero para revisiones periódicas van perfectos y no necesitas mantener nada “encendido”.

## Agregar nuevas obras
Edita `manga_library.yaml` y añade un bloque como:
```yaml
- name: "¿La nueva jefa es mi exnovia?"
  site: "zonatmo.com"
  url: "https://zonatmo.com/library/manhua/84698/lanuevajefaesmiexnovia"
  last_chapter: null
```

## ¿Cómo evita falsos positivos?
- El script detecta el **máximo número** de capítulo visible en la página.
- Si nunca guardaste `last_seen`, no notifica hasta que haya una siguiente corrida comparativa **o** hasta que ejecutes el flujo manual con `--save` (el paso “Update baseline”).
- Para páginas muy dinámicas (como `m440.in`) tal vez necesites ajustar el selector; avísame y lo pulimos.

## Alternativas “siempre encendido” (opcional)
- **UptimeRobot** o servicios de “health check” no aplican aquí porque no alojamos un servidor; pero **GitHub Actions programado** ya cumple la función de revisar y notificar.
- Si prefieres un microservidor, puedes usar un contenedor en alguna plataforma y agendar con `cron`, pero normalmente gastarías créditos gratis y se “dormiría” al no recibir tráfico. Por eso Actions es la vía más simple sin apagones.
