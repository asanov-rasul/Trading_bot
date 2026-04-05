import os
import logging
import asyncio
import nest_asyncio
import requests
import pandas as pd
import numpy as np
from flask import Flask, request as flask_request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

nest_asyncio.apply()

# ─── Переменные окружения ───
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8776897403:AAHQg_1L-SOEnZVWkWE0A5lxlSa6YqVFWuY")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "https://trading-bot-7mtw.onrender.com")   # https://trading-bot-7mtw.onrender.com
PORT           = int(os.environ.get("PORT", 10000))

PAIRS = {
    "BTC":   "BTCUSDT",
    "SOL":   "SOLUSDT",
    "RIVER": "RIVERUSDT",
    "ETH":   "ETHUSDT",
    "BNB":   "BNBUSDT",
    "XRP":   "XRPUSDT",
}

BINANCE_URL = "https://api.binance.com/api/v3/klines"
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)


# ══════════════════════════════════════════
#  BINANCE
# ══════════════════════════════════════════
def fetch_candles(symbol: str, interval: str, limit: int = 250) -> pd.DataFrame:
    r = requests.get(BINANCE_URL, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
    r.raise_for_status()
    df = pd.DataFrame(r.json(), columns=[
        "time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    return df


# ══════════════════════════════════════════
#  ИНДИКАТОРЫ
# ══════════════════════════════════════════
def calc_rsi(close, period=14):
    delta = close.diff()
    avg_g = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    avg_l = (-delta.clip(upper=0)).ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return round(float((100 - 100/(1+rs)).iloc[-1]), 2)

def calc_macd(close):
    m = close.ewm(span=12,adjust=False).mean() - close.ewm(span=26,adjust=False).mean()
    s = m.ewm(span=9,adjust=False).mean()
    h = m - s
    return round(float(m.iloc[-1]),4), round(float(s.iloc[-1]),4), round(float(h.iloc[-1]),4)

def calc_ema(close, period):
    return round(float(close.ewm(span=period,adjust=False).mean().iloc[-1]),4)

def calc_volume_trend(volume, window=20):
    avg = volume.iloc[-window:-1].mean()
    last = volume.iloc[-1]
    return "📈 Растёт" if last > avg*1.1 else ("📉 Падает" if last < avg*0.9 else "➡️ Нейтральный")

def find_levels(df, lookback=50):
    h = df["high"].iloc[-lookback:]
    l = df["low"].iloc[-lookback:]
    return (round(float(l.min()),4), round(float(l.nsmallest(5).mean()),4),
            round(float(h.nlargest(5).mean()),4), round(float(h.max()),4))

def detect_pattern(df):
    o,h,l,c = (df[x].iloc[-3:].values for x in ["open","high","low","close"])
    body = abs(c-o); total = h-l+1e-9
    is_bull = c[-1]>o[-1]; is_bear = c[-1]<o[-1]
    if body[-1]/total[-1] < 0.1: return "🕯 Доджи"
    ls = (o[-1]-l[-1]) if is_bull else (c[-1]-l[-1])
    us = (h[-1]-c[-1]) if is_bull else (h[-1]-o[-1])
    if ls > 2*body[-1] and us < body[-1]*0.5: return "🔨 Молот"
    if us > 2*body[-1] and ls < body[-1]*0.5 and is_bear: return "⭐ Падающая звезда"
    if len(c) >= 2:
        if c[-2]<o[-2] and c[-1]>o[-1] and c[-1]>o[-2] and o[-1]<c[-2]: return "🟢 Бычье поглощение"
        if c[-2]>o[-2] and c[-1]<o[-1] and c[-1]<o[-2] and o[-1]>c[-2]: return "🔴 Медвежье поглощение"
    return "🟢 Бычья свеча" if is_bull else "🔴 Медвежья свеча"


# ══════════════════════════════════════════
#  ГЕНЕРАЦИЯ СИГНАЛА
# ══════════════════════════════════════════
def generate_signal(symbol: str) -> str:
    try:
        df_1h = fetch_candles(symbol, "1h", 250)
        df_4h = fetch_candles(symbol, "4h", 200)
    except Exception as e:
        return f"❌ Ошибка Binance API:\n`{e}`"

    price  = round(df_1h["close"].iloc[-1], 4)
    rsi    = calc_rsi(df_1h["close"])
    ml,ms,mh = calc_macd(df_1h["close"])
    ema50  = calc_ema(df_1h["close"], 50)
    ema200 = calc_ema(df_1h["close"], 200)
    vol    = calc_volume_trend(df_1h["volume"])
    pat    = detect_pattern(df_1h)
    sup,s2,r2,res = find_levels(df_1h)
    rsi_4h = calc_rsi(df_4h["close"])
    _,_,mh4 = calc_macd(df_4h["close"])
    atr    = round(float((df_1h["high"]-df_1h["low"]).iloc[-20:].mean()), 4)

    rsi_txt  = f"{rsi} 🔴 Перекуплен" if rsi>=70 else (f"{rsi} 🟢 Перепродан" if rsi<=30 else f"{rsi} ⚪ Нейтрально")
    macd_txt = (f"Бычий ↑ (hist: {mh:+.4f})" if mh>0 and ml>ms
                else (f"Медвежий ↓ (hist: {mh:+.4f})" if mh<0 and ml<ms else f"Нейтраль (hist: {mh:+.4f})"))
    ema_txt  = (f"EMA50 > EMA200 ✅ Бычий | {ema50}/{ema200}" if ema50>ema200
                else f"EMA50 < EMA200 ❌ Медвежий | {ema50}/{ema200}")

    b = br = 0
    if rsi < 40: b += 2
    elif rsi > 60: br += 2
    if rsi < 50: b += 1
    else: br += 1
    if mh > 0 and ml > ms: b += 2
    elif mh < 0 and ml < ms: br += 2
    if ema50 > ema200: b += 2
    else: br += 2
    if price > ema50: b += 1
    else: br += 1
    if rsi_4h < 50 and mh4 > 0: b += 1
    elif rsi_4h > 50 and mh4 < 0: br += 1
    if "Бычье" in pat or "Молот" in pat: b += 1
    if "Медвежье" in pat or "Звезда" in pat: br += 1

    diff = b - br
    if abs(diff) < 3:
        return (f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🪙 *{symbol}* | `{price}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚠️ *Сигнала нет*\n\n"
                f"• RSI: {rsi_txt}\n• MACD: {macd_txt}\n• {ema_txt}\n\n"
                f"_Рынок неопределённый. Жди чёткого движения._")

    direction = "long" if diff >= 3 else "short"
    trend     = "📈 ЛОНГ" if direction == "long" else "📉 ШОРТ"

    if direction == "long":
        entry=round(price*1.001,4); sl=round(entry-atr*1.5,4)
        tp1=round(entry+atr*1.5,4); tp2=round(entry+atr*3.0,4); tp3=round(entry+atr*5.0,4)
    else:
        entry=round(price*0.999,4); sl=round(entry+atr*1.5,4)
        tp1=round(entry-atr*1.5,4); tp2=round(entry-atr*3.0,4); tp3=round(entry-atr*5.0,4)

    rr = round(abs(entry-tp2)/max(abs(entry-sl), 0.0001), 2)

    return (f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *{symbol}* | `{price}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"*Тренд:* {trend}\n\n"
            f"*📊 Анализ (1H):*\n"
            f"• RSI: {rsi_txt}\n"
            f"• MACD: {macd_txt}\n"
            f"• EMA: {ema_txt}\n"
            f"• Поддержка: `{sup}` / `{s2}`\n"
            f"• Сопротивление: `{r2}` / `{res}`\n"
            f"• Объём: {vol}\n"
            f"• Паттерн: {pat}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Вход:* `{entry}`\n"
            f"🛑 *Стоп-лосс:* `{sl}`\n\n"
            f"🎯 *Тейк-профит:*\n"
            f"  TP1: `{tp1}`\n"
            f"  TP2: `{tp2}`\n"
            f"  TP3: `{tp3}`\n\n"
            f"📊 *Риск/Прибыль:* `1:{rr}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"_4H RSI: {rsi_4h} | ATR: {atr}_")


# ══════════════════════════════════════════
#  TELEGRAM HANDLERS
# ══════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Crypto Signal Bot*\n\n"
        "Напиши тикер и получишь торговый сигнал:\n\n"
        "`BTC` `SOL` `ETH` `BNB` `XRP` `RIVER`\n\n"
        "📡 Данные с Binance в реальном времени",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text   = update.message.text.strip().upper()
    symbol = next((pair for key, pair in PAIRS.items() if text in (key, pair)), None)
    if not symbol:
        await update.message.reply_text(
            f"❓ Не знаю пару *{text}*\n\nПопробуй: `BTC` `SOL` `ETH` `BNB` `XRP`",
            parse_mode="Markdown"
        )
        return
    msg = await update.message.reply_text(f"⏳ Анализирую *{symbol}*...", parse_mode="Markdown")
    await msg.edit_text(generate_signal(symbol), parse_mode="Markdown")


# ══════════════════════════════════════════
#  FLASK + WEBHOOK
# ══════════════════════════════════════════
flask_app = Flask(__name__)

# Создаём Application один раз
ptb_app = Application.builder().token(TELEGRAM_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

loop = asyncio.get_event_loop()
loop.run_until_complete(ptb_app.initialize())


@flask_app.get("/")
def index():
    return "✅ Crypto Signal Bot is running!", 200


@flask_app.post("/webhook")
def webhook():
    data   = flask_request.get_json(force=True)
    update = Update.de_json(data, ptb_app.bot)
    loop.run_until_complete(ptb_app.process_update(update))
    return "ok", 200


@flask_app.get("/set_webhook")
def set_webhook():
    url = f"{WEBHOOK_URL}/webhook"
    loop.run_until_complete(ptb_app.bot.set_webhook(url=url))
    return f"✅ Webhook установлен: {url}", 200


if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=PORT)
