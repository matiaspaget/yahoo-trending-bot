"""
Yahoo Finance Trending Tickers → Telegram Bot
Monitorea cada 5 minutos los tickers en tendencia y avisa por Telegram cuando aparece uno nuevo.

Configuración requerida (variables de entorno):
  TELEGRAM_BOT_TOKEN  → Token de tu bot (lo da @BotFather)
  TELEGRAM_CHAT_ID    → ID de tu chat donde llegan las alertas
"""

import os
import json
import time
import logging
import requests
from bs4 import BeautifulSoup

# ── Configuración ─────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

CHECK_INTERVAL_SECONDS = 5 * 60
SEEN_TICKERS_FILE      = "seen_tickers.json"

YAHOO_URL = "https://finance.yahoo.com/markets/stocks/trending/?start=0&count=25"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Persistencia de tickers ya vistos ─────────────────────────────────────────

def load_seen_tickers() -> set:
    if os.path.exists(SEEN_TICKERS_FILE):
        with open(SEEN_TICKERS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_tickers(tickers: set):
    with open(SEEN_TICKERS_FILE, "w") as f:
        json.dump(list(tickers), f)

# ── Scraping de Yahoo Finance ─────────────────────────────────────────────────

def fetch_trending_tickers() -> list[dict]:
    """
    Parsea la página de trending de Yahoo Finance y devuelve lista de tickers.
    Cada dict tiene: symbol, name, price, change_pct
    """
    try:
        resp = requests.get(YAHOO_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        tickers = []

        # Método 1: parsear tabla
        rows = soup.select("table tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            symbol_el = cells[0].find("span") or cells[0]
            symbol = symbol_el.get_text(strip=True)
            name_el = cells[1].find("span") or cells[1]
            name = name_el.get_text(strip=True)
            price = None
            change_pct = None
            if len(cells) >= 4:
                try:
                    price = float(cells[2].get_text(strip=True).replace(",", ""))
                except:
                    pass
                try:
                    pct_text = cells[4].get_text(strip=True).replace("%", "").replace("+", "")
                    change_pct = float(pct_text)
                except:
                    pass
            if symbol and len(symbol) <= 10 and symbol.isupper():
                tickers.append({
                    "symbol": symbol, "name": name or symbol,
                    "price": price, "change_pct": change_pct,
                })

        # Método 2 (fallback): buscar links /quote/
        if not tickers:
            log.warning("Tabla no encontrada, usando método alternativo...")
            seen_syms = set()
            for link in soup.select('a[href*="/quote/"]'):
                href = link.get("href", "")
                parts = href.split("/quote/")
                if len(parts) < 2:
                    continue
                symbol = parts[1].split("/")[0].split("?")[0]
                if symbol and symbol.isupper() and 1 <= len(symbol) <= 10 and symbol not in seen_syms:
                    seen_syms.add(symbol)
                    tickers.append({
                        "symbol": symbol, "name": link.get_text(strip=True) or symbol,
                        "price": None, "change_pct": None,
                    })

        log.info(f"Tickers obtenidos: {len(tickers)}")
        return tickers

    except Exception as e:
        log.error(f"Error al obtener tickers: {e}")
        return []

# ── Enviar mensaje por Telegram ───────────────────────────────────────────────

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram no configurado. Revisá TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info(f"Telegram ✓: {message[:60]}...")
    except Exception as e:
        log.error(f"Error enviando Telegram: {e}")

def format_alert(ticker: dict) -> str:
    symbol     = ticker["symbol"]
    name       = ticker["name"]
    price      = ticker["price"]
    change_pct = ticker["change_pct"]

    arrow = "🟢" if (change_pct or 0) >= 0 else "🔴"
    price_str  = f"${price:.2f}"  if price      is not None else "N/D"
    change_str = f"{change_pct:+.2f}%" if change_pct is not None else "N/D"

    yahoo_url = f"https://finance.yahoo.com/quote/{symbol}/"

    return (
        f"🔥 <b>Nuevo ticker en tendencia</b>\n"
        f"\n"
        f"📌 <b>{symbol}</b> — {name}\n"
        f"{arrow} Precio: {price_str}  ({change_str})\n"
        f"\n"
        f'<a href="{yahoo_url}">Ver en Yahoo Finance</a>'
    )

# ── Loop principal ────────────────────────────────────────────────────────────

def main():
    log.info("=== Yahoo Finance Trending Monitor iniciado ===")
    log.info(f"Intervalo de chequeo: {CHECK_INTERVAL_SECONDS // 60} minutos")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error(
            "¡Faltan variables de entorno! "
            "Configurá TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID antes de iniciar."
        )

    seen = load_seen_tickers()
    log.info(f"Tickers ya conocidos: {len(seen)}")

    # Primera corrida: cargar el estado actual sin alertar (para no spammear al iniciar)
    if not seen:
        log.info("Primera ejecución: registrando tickers actuales sin enviar alertas...")
        initial = fetch_trending_tickers()
        for t in initial:
            seen.add(t["symbol"])
        save_seen_tickers(seen)
        log.info(f"  → {len(seen)} tickers registrados: {', '.join(sorted(seen))}")

    send_telegram(
        "✅ <b>Yahoo Finance Monitor activo</b>\n"
        f"Revisaré los trending tickers cada {CHECK_INTERVAL_SECONDS // 60} minutos "
        "y te avisaré cuando aparezca uno nuevo."
    )

    while True:
        log.info("Consultando Yahoo Finance...")
        tickers = fetch_trending_tickers()

        if not tickers:
            log.warning("No se obtuvieron tickers. Reintentando en el próximo ciclo.")
        else:
            new_tickers = [t for t in tickers if t["symbol"] not in seen]

            if new_tickers:
                log.info(f"¡{len(new_tickers)} ticker(s) nuevo(s) detectado(s)!")
                for t in new_tickers:
                    log.info(f"  → {t['symbol']} ({t['name']})")
                    send_telegram(format_alert(t))
                    seen.add(t["symbol"])
                save_seen_tickers(seen)
            else:
                current_symbols = [t["symbol"] for t in tickers]
                log.info(f"Sin novedades. Actuales: {', '.join(current_symbols)}")

        log.info(f"Esperando {CHECK_INTERVAL_SECONDS // 60} minutos...")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
