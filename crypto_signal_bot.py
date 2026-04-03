"""
╔══════════════════════════════════════╗
║   CRYPTO SIGNAL BOT — Telegram       ║
║   Данные: Binance API (бесплатно)    ║
║   Индикаторы: RSI, MACD, EMA 50/200  ║
╚══════════════════════════════════════╝

Установка зависимостей:
    pip install python-telegram-bot requests pandas numpy

Запуск:
    python crypto_signal_bot.py

Команды в боте:
    BTC / SOL / RIVER  →  торговый сигнал
    /start             →  приветствие
    /help              →  список команд
"""

import logging
import requests
import pandas as pd
import numpy as np
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ──────────────────────────────────────────
#  🔑  ВСТАВЬ СВОЙ ТОКЕН СЮДА
# ──────────────────────────────────────────
TELEGRAM_TOKEN = "8776897403:AAHQg_1L-SOEnZVWkWE0A5lxlSa6YqVFWuY"

# ──────────────────────────────────────────
#  Маппинг тикеров → пары Binance
# ──────────────────────────────────────────
PAIRS = {
    "BTC":   "BTCUSDT",
    "SOL":   "SOLUSDT",
    "RIVER": "RIVERUSDT",
    "ETH":   "ETHUSDT",
    "BNB":   "BNBUSDT",
    "XRP":   "XRPUSDT",
}

BINANCE_URL = "https://api.binance.com/api/v3/klines"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)


# ══════════════════════════════════════════
#  ЗАГРУЗКА СВЕЧЕЙ С BINANCE
# ══════════════════════════════════════════
def fetch_candles(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    """Получить OHLCV данные с Binance."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(BINANCE_URL, params=params, timeout=10)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        raise ConnectionError(f"Binance API error: {e}")

    df = pd.DataFrame(raw, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ══════════════════════════════════════════
#  ИНДИКАТОРЫ
# ══════════════════════════════════════════
def calc_rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def calc_macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return (
        round(float(macd_line.iloc[-1]), 4),
        round(float(signal_line.iloc[-1]), 4),
        round(float(histogram.iloc[-1]), 4),
    )


def calc_ema(close: pd.Series, period: int) -> float:
    return round(float(close.ewm(span=period, adjust=False).mean().iloc[-1]), 4)


def calc_volume_trend(volume: pd.Series, window: int = 20) -> str:
    avg = volume.iloc[-window:-1].mean()
    last = volume.iloc[-1]
    return "📈 Растёт" if last > avg * 1.1 else ("📉 Падает" if last < avg * 0.9 else "➡️ Нейтральный")


def find_levels(df: pd.DataFrame, lookback: int = 50):
    """Простые уровни поддержки/сопротивления через локальные экстремумы."""
    highs = df["high"].iloc[-lookback:]
    lows  = df["low"].iloc[-lookback:]
    resistance = round(float(highs.max()), 4)
    support    = round(float(lows.min()), 4)
    # Ближайшие промежуточные уровни
    r2 = round(float(highs.nlargest(5).mean()), 4)
    s2 = round(float(lows.nsmallest(5).mean()), 4)
    return support, s2, r2, resistance


def detect_pattern(df: pd.DataFrame) -> str:
    """Простые свечные паттерны по последним 3 свечам."""
    o, h, l, c = (df[x].iloc[-3:].values for x in ["open", "high", "low", "close"])
    body = abs(c - o)
    total_range = h - l + 1e-9

    # Последняя свеча
    b = body[-1]; t = total_range[-1]
    is_bull = c[-1] > o[-1]
    is_bear = c[-1] < o[-1]

    # Доджи
    if b / t < 0.1:
        return "🕯 Доджи (неопределённость)"

    # Молот / Повешенный
    lower_shadow = o[-1] - l[-1] if is_bull else c[-1] - l[-1]
    upper_shadow = h[-1] - c[-1] if is_bull else h[-1] - o[-1]
    if lower_shadow > 2 * b and upper_shadow < b * 0.5:
        return "🔨 Молот (возможный разворот вверх)"

    # Падающая звезда
    if upper_shadow > 2 * b and lower_shadow < b * 0.5 and is_bear:
        return "⭐ Падающая звезда (разворот вниз)"

    # Бычье/медвежье поглощение
    if len(c) >= 2:
        if c[-2] < o[-2] and c[-1] > o[-1] and c[-1] > o[-2] and o[-1] < c[-2]:
            return "🟢 Бычье поглощение"
        if c[-2] > o[-2] and c[-1] < o[-1] and c[-1] < o[-2] and o[-1] > c[-2]:
            return "🔴 Медвежье поглощение"

    if is_bull:
        return "🟢 Бычья свеча"
    return "🔴 Медвежья свеча"


# ══════════════════════════════════════════
#  ГЕНЕРАЦИЯ СИГНАЛА
# ══════════════════════════════════════════
def generate_signal(symbol: str) -> str:
    try:
        # Данные по трём таймфреймам
        df_15m = fetch_candles(symbol, "15m", 250)
        df_1h  = fetch_candles(symbol, "1h",  250)
        df_4h  = fetch_candles(symbol, "4h",  200)
    except ConnectionError as e:
        return f"❌ Ошибка подключения к Binance:\n{e}"

    price = round(df_1h["close"].iloc[-1], 4)

    # ── Индикаторы 1H (основной ТФ) ──
    rsi_1h   = calc_rsi(df_1h["close"])
    macd_line, macd_signal, macd_hist = calc_macd(df_1h["close"])
    ema50    = calc_ema(df_1h["close"], 50)
    ema200   = calc_ema(df_1h["close"], 200)
    vol_txt  = calc_volume_trend(df_1h["volume"])
    pattern  = detect_pattern(df_1h)
    sup, s2, r2, res = find_levels(df_1h)

    # ── Подтверждение 4H ──
    rsi_4h   = calc_rsi(df_4h["close"])
    macd_4h_line, macd_4h_sig, macd_4h_hist = calc_macd(df_4h["close"])

    # ── RSI текст ──
    if rsi_1h >= 70:
        rsi_txt = f"{rsi_1h} 🔴 Перекуплен"
    elif rsi_1h <= 30:
        rsi_txt = f"{rsi_1h} 🟢 Перепродан"
    else:
        rsi_txt = f"{rsi_1h} ⚪ Нейтрально"

    # ── MACD текст ──
    if macd_hist > 0 and macd_line > macd_signal:
        macd_txt = f"Бычий импульс ↑ (hist: {macd_hist:+.4f})"
    elif macd_hist < 0 and macd_line < macd_signal:
        macd_txt = f"Медвежий импульс ↓ (hist: {macd_hist:+.4f})"
    else:
        macd_txt = f"Пересечение/Нейтраль (hist: {macd_hist:+.4f})"

    # ── EMA текст ──
    if ema50 > ema200:
        ema_txt = f"EMA50 > EMA200 ✅ Бычий крест | {ema50} / {ema200}"
    else:
        ema_txt = f"EMA50 < EMA200 ❌ Медвежий крест | {ema50} / {ema200}"

    # ══════════════════════════
    #  ЛОГИКА СИГНАЛА
    # ══════════════════════════
    bull_score = 0
    bear_score = 0

    # RSI
    if rsi_1h < 40: bull_score += 2
    elif rsi_1h > 60: bear_score += 2
    if rsi_1h < 50: bull_score += 1
    else: bear_score += 1

    # MACD
    if macd_hist > 0 and macd_line > macd_signal:   bull_score += 2
    elif macd_hist < 0 and macd_line < macd_signal: bear_score += 2

    # EMA
    if ema50 > ema200: bull_score += 2
    else:              bear_score += 2

    # Цена vs EMA50
    if price > ema50: bull_score += 1
    else:             bear_score += 1

    # 4H подтверждение
    if rsi_4h < 50 and macd_4h_hist > 0: bull_score += 1
    elif rsi_4h > 50 and macd_4h_hist < 0: bear_score += 1

    # Паттерн
    if "Бычье" in pattern or "Молот" in pattern: bull_score += 1
    if "Медвежье" in pattern or "Звезда" in pattern: bear_score += 1

    # ── Принятие решения ──
    score_diff = bull_score - bear_score

    if score_diff >= 3:
        trend = "📈 ЛОНГ"
        direction = "long"
    elif score_diff <= -3:
        trend = "📉 ШОРТ"
        direction = "short"
    else:
        # Сигнала нет — слишком неопределённо
        return (
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *{symbol}* | Цена: `{price}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚠️ *Сигнала нет*\n\n"
            f"Рынок неопределённый.\n"
            f"• RSI (1H): {rsi_txt}\n"
            f"• MACD: {macd_txt}\n"
            f"• {ema_txt}\n\n"
            f"_Жди чёткого пробоя или паттерна._"
        )

    # ── Расчёт уровней входа/выхода ──
    atr_proxy = round(float((df_1h["high"] - df_1h["low"]).iloc[-20:].mean()), 4)

    if direction == "long":
        entry      = round(price * 1.001, 4)           # чуть выше текущей
        stop_loss  = round(entry - atr_proxy * 1.5, 4)
        tp1        = round(entry + atr_proxy * 1.5, 4)
        tp2        = round(entry + atr_proxy * 3.0, 4)
        tp3        = round(entry + atr_proxy * 5.0, 4)
    else:
        entry      = round(price * 0.999, 4)
        stop_loss  = round(entry + atr_proxy * 1.5, 4)
        tp1        = round(entry - atr_proxy * 1.5, 4)
        tp2        = round(entry - atr_proxy * 3.0, 4)
        tp3        = round(entry - atr_proxy * 5.0, 4)

    risk   = abs(entry - stop_loss)
    reward = abs(entry - tp2)
    rr     = round(reward / risk, 2) if risk > 0 else 0

    # ── Финальный вывод ──
    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 *{symbol}* | Цена: `{price}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*Тренд:* {trend}\n\n"
        f"*📊 Анализ (1H):*\n"
        f"• RSI: {rsi_txt}\n"
        f"• MACD: {macd_txt}\n"
        f"• EMA: {ema_txt}\n"
        f"• Поддержка: `{sup}` / `{s2}`\n"
        f"• Сопротивление: `{r2}` / `{res}`\n"
        f"• Объём: {vol_txt}\n"
        f"• Паттерн: {pattern}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 *Вход:* `{entry}`\n"
        f"🛑 *Стоп-лосс:* `{stop_loss}`\n\n"
        f"🎯 *Тейк-профит:*\n"
        f"  TP1: `{tp1}`\n"
        f"  TP2: `{tp2}`\n"
        f"  TP3: `{tp3}`\n\n"
        f"📊 *Риск/Прибыль:* `1:{rr}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_4H RSI: {rsi_4h} | ATR: {atr_proxy}_"
    )
    return msg


# ══════════════════════════════════════════
#  TELEGRAM HANDLERS
# ══════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Crypto Signal Bot*\n\n"
        "Напиши тикер — получишь торговый сигнал:\n\n"
        "• `BTC` — Bitcoin\n"
        "• `SOL` — Solana\n"
        "• `ETH` — Ethereum\n"
        "• `BNB` — BNB\n"
        "• `XRP` — Ripple\n"
        "• `RIVER` — River (если есть на Binance)\n\n"
        "Данные берутся с Binance в реальном времени 📡",
        parse_mode="Markdown"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 *Список команд:*\n\n"
        "`/start` — приветствие\n"
        "`/help`  — эта справка\n\n"
        "*Просто напиши тикер:*\n"
        "`BTC`, `SOL`, `ETH`, `BNB`, `XRP`\n\n"
        "⏱ Таймфреймы: 15m • 1H • 4H\n"
        "📡 Источник: Binance Public API",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()

    # Нормализация ввода
    symbol = None
    for key, pair in PAIRS.items():
        if text in (key, pair):
            symbol = pair
            break

    if not symbol:
        await update.message.reply_text(
            f"❓ Не знаю такую пару: *{text}*\n\n"
            f"Попробуй: `BTC`, `SOL`, `ETH`, `BNB`, `XRP`",
            parse_mode="Markdown"
        )
        return

    # Индикатор загрузки
    msg = await update.message.reply_text(f"⏳ Анализирую *{symbol}*...", parse_mode="Markdown")

    signal = generate_signal(symbol)

    await msg.edit_text(signal, parse_mode="Markdown")


# ══════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════
def main():
    if TELEGRAM_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Вставь свой TELEGRAM_TOKEN в переменную TELEGRAM_TOKEN!")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🤖 Бот запущен. Нажми Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()
