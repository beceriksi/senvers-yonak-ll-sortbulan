import requests
import pandas as pd
import numpy as np
import os
import time
import json

TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# STRATEJİ AYARLARI
FUNDING_LIMIT = 0.02
RSI_LIMIT = 70
CHANGE_24H_LIMIT = 8
WHALE_WALL_RATIO = 2.5
HISTORY_FILE = "signal_history.json"

def send_telegram(msg):
    if TOKEN and CHAT_ID:
        # Mesajın başına SHORT BOTU ibaresi eklendi
        full_msg = f"📉 *[SHORT BOTU]*\n{msg}"
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": full_msg, "parse_mode": "Markdown"})

def get_data(endpoint, params={}):
    base = "https://www.okx.com"
    try:
        res = requests.get(base + endpoint, params=params).json()
        return res.get('data', [])
    except: return []

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_market_trend():
    btc = get_data("/api/v5/market/tickers", {"instId": "BTC-USDT-SWAP"})
    if btc:
        change = (float(btc[0]['last']) / float(btc[0]['open24h']) - 1) * 100
        return f"%{round(change, 2)} {'📉' if change < 0 else '📈'}"
    return "BELİRSİZ"

def check_whale_walls(symbol):
    depth = get_data("/api/v5/market/books", {"instId": symbol, "sz": "20"})
    if not depth: return 1, 0
    asks = sum([float(a[1]) for a in depth[0]['asks']])
    bids = sum([float(b[1]) for b in depth[0]['bids']])
    return (asks / bids if bids > 0 else 1), asks

def analyze_signal(rsi, f_rate, wall_ratio, change):
    score = 5.0
    warnings = []
    if rsi > 85: 
        score += 2.0
        warnings.append("🔥 AŞIRI ŞİŞME: RSI 85 üstünde.")
    elif rsi > 75: score += 1.0
    if f_rate < -0.1:
        score -= 3.0
        warnings.append("🚨 TEHLİKE: Funding aşırı negatif! Squeeze riski.")
    elif f_rate < 0:
        score -= 1.0
        warnings.append("⚠️ UYARI: Funding negatif.")
    elif f_rate > 0.02:
        score += 1.5
        warnings.append("💎 TEMİZ: Funding pozitif.")
    if wall_ratio > 3.0:
        score += 1.5
        warnings.append("🐋 BALİNA DİRENCİ: Güçlü satış duvarı.")
    return round(max(1, min(10, score)), 1), warnings

def manage_history(symbol, score, rsi, f_rate, wall_ratio):
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f: json.dump({}, f)
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
    
    update_msg = ""
    if symbol in history:
        old = history[symbol]
        if wall_ratio < old['wall'] * 0.6:
            update_msg += f"⚠️ *{symbol}*: Balina duvarı zayıfladı! (%{round(old['wall'],1)}x -> %{round(wall_ratio,1)}x)\n"
        if f_rate < old['funding'] - 0.05:
            update_msg += f"🚨 *{symbol}*: Fonlama negatife kaydı! Tehlike.\n"
        if score < old['score'] - 2:
            update_msg += f"❌ *{symbol}*: Güven puanı düştü! ({old['score']} -> {score})\n"
    
    history[symbol] = {"score": score, "rsi": rsi, "funding": f_rate, "wall": wall_ratio, "time": time.time()}
    with open(HISTORY_FILE, "w") as f: json.dump(history, f)
    return update_msg

def scan():
    trend = get_market_trend()
    tickers = get_data("/api/v5/market/tickers", {"instType": "SWAP"})
    tickers = sorted(tickers, key=lambda x: float(x['vol24h']), reverse=True)[:100]
    final_alerts = []
    
    for t in tickers:
        symbol = t['instId']
        if "-USDT-" not in symbol: continue
        change = (float(t['last']) / float(t['open24h']) - 1) * 100
        if change > CHANGE_24H_LIMIT:
            candles = get_data("/api/v5/market/candles", {"instId": symbol, "bar": "1H", "limit": "50"})
            if not candles: continue
            df = pd.DataFrame(candles, columns=['ts','o','h','l','c','v','vc','vq','conf'])
            df['c'] = df['c'].astype(float)
            rsi = calculate_rsi(df['c'][::-1]).iloc[-1]
            funding = get_data("/api/v5/public/funding-rate", {"instId": symbol})
            f_rate = float(funding[0]['fundingRate']) * 100 if funding else 0
            wall_ratio, _ = check_whale_walls(symbol)
            
            score, warnings = analyze_signal(rsi, f_rate, wall_ratio, change)
            update_notif = manage_history(symbol, score, rsi, f_rate, wall_ratio)
            
            if update_notif:
                send_telegram(f"🔄 *AKTİF TAKİP UYARISI*\n\n{update_notif}")

            if score >= 6.0:
                warning_text = "\n".join([f"- {w}" for w in warnings])
                msg = (f"🚀 *GÜVEN PUANI: {score}/10*\n💎 *PARİTE: {symbol}*\n\n"
                       f"🌍 BTC: {trend} | 📈 Değişim: %{round(change, 2)}\n"
                       f"📊 RSI: {round(rsi, 2)} | 💸 Fund: %{round(f_rate, 4)}\n"
                       f"🧱 Duvar: {round(wall_ratio, 1)}x\n\n💡 *NOTLAR:*\n{warning_text if warning_text else '- Stabil.'}")
                final_alerts.append((score, msg))

    final_alerts.sort(key=lambda x: x[0], reverse=True)
    for _, m in final_alerts: send_telegram(m)

if __name__ == "__main__":
    scan()
