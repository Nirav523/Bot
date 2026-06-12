import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone, timedelta
import sys

# ============ CONFIGURATION ============
API_KEY = "9625492f7453451ba2b0a168a029a479"
TELEGRAM_BOT_TOKEN = "8892424969:AAEtTlUMt0JOM9jjC6MhH_tjV3Z5dYSKtIo"
TELEGRAM_CHAT_ID = "854168042"

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Pair settings
PAIRS = [
    {"symbol": "EUR/USD", "name": "EURUSD", "adx": 12, "rsi_buy": 42, "rsi_sell": 58, "stoch_buy": 32, "stoch_sell": 68, "bb": 0.5, "max_daily": 8, "tp_dollar": 32},
    {"symbol": "GBP/USD", "name": "GBPUSD", "adx": 12, "rsi_buy": 45, "rsi_sell": 55, "stoch_buy": 35, "stoch_sell": 65, "bb": 0.5, "max_daily": 8, "tp_dollar": 32},
    {"symbol": "XAU/USD", "name": "GOLD", "adx": 12, "rsi_buy": 38, "rsi_sell": 62, "stoch_buy": 28, "stoch_sell": 72, "bb": 0.5, "max_daily": 8, "tp_dollar": 40},
]

# Session in UTC (IST = UTC+5:30)
# 08-17 UTC = 1:30 PM - 10:30 PM IST
SESSION_START = 8
SESSION_END = 17
SL_ATR = 1.5
TP_ATR_FOREX = 2.5
TP_ATR_GOLD = 3.0
SKIP_HOURS = [10, 12, 17]  # UTC hours to skip

active_trades = {}
daily_trades = {}

def get_ist_time(dt=None):
    """Convert datetime to IST 12-hour format string"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    ist_time = dt.astimezone(IST)
    return ist_time.strftime('%d %b %Y, %I:%M %p IST')

def get_ist_time_short(dt=None):
    """Convert datetime to IST 12-hour short format"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    ist_time = dt.astimezone(IST)
    return ist_time.strftime('%I:%M %p IST')

def send_telegram(message):
    """Send message to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def ema(s, p): return s.ewm(span=p, adjust=False).mean()
def rsi(s, p):
    d = s.diff(); g = d.where(d > 0, 0); l = -d.where(d < 0, 0)
    return 100 - (100 / (1 + g.rolling(p).mean() / l.rolling(p).mean()))
def stoch(df, kp, sm):
    lm = df['low'].rolling(kp).min(); hm = df['high'].rolling(kp).max()
    k = 100 * (df['close'] - lm) / (hm - lm)
    return k, k.rolling(sm).mean()
def atr(df, p):
    h, l, c = df['high'], df['low'], df['close']
    tr = pd.concat([h - l, abs(h - c.shift()), abs(l - c.shift())], axis=1).max(axis=1)
    return tr.rolling(p).mean()
def bb(s, p, sd):
    m = s.rolling(p).mean(); std = s.rolling(p).std()
    return m + sd * std, m, m - sd * std
def adx_func(df, p):
    h, l, c = df['high'], df['low'], df['close']; tr = atr(df, p)
    up = h.diff(); dn = -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0); ndm = dn.where((dn > up) & (dn > 0), 0)
    atr_avg = tr.rolling(p).mean()
    pdi = 100 * (pdm.rolling(p).mean() / atr_avg)
    ndi = 100 * (ndm.rolling(p).mean() / atr_avg)
    dx = 100 * abs(pdi - ndi) / (pdi + ndi)
    return dx.rolling(p).mean(), pdi, ndi

def fetch_data(symbol):
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval=15min&outputsize=500&apikey={API_KEY}"
        resp = requests.get(url, timeout=15).json()
        if "values" not in resp: return None
        df = pd.DataFrame(resp["values"]).iloc[::-1].reset_index(drop=True)
        df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)
        df['datetime'] = pd.to_datetime(df['datetime'])
        return df
    except: return None

def calculate_indicators(df):
    df['ef'] = ema(df['close'], 20)
    df['es'] = ema(df['close'], 50)
    df['rsi'] = rsi(df['close'], 7)
    df['sk'], df['sd'] = stoch(df, 14, 3)
    df['atr'] = atr(df, 14)
    df['bu'], df['bm'], df['bl'] = bb(df['close'], 20, 2)
    df['adx'], _, _ = adx_func(df, 14)
    return df

def check_entry(df, pair_config):
    name = pair_config['name']
    row = df.iloc[-1]
    h = row['datetime'].hour
    d = row['datetime'].strftime('%Y-%m-%d')
    
    if h < SESSION_START or h > SESSION_END: return None
    if h in SKIP_HOURS: return None
    if daily_trades.get(d, {}).get(name, 0) >= pair_config['max_daily']: return None
    if name in active_trades: return None
    if row['adx'] < pair_config['adx']: return None
    
    e = row['close']
    tp_atr = TP_ATR_GOLD if name == "GOLD" else TP_ATR_FOREX
    
    if (e <= row['bl'] + pair_config['bb'] * row['atr'] and 
        row['rsi'] < pair_config['rsi_buy'] and 
        row['sk'] < pair_config['stoch_buy'] and 
        row['sk'] > row['sd']):
        
        sl = e - SL_ATR * row['atr']
        tp = e + tp_atr * row['atr']
        return {'pair': name, 'sig': 'BUY', 'e': e, 'sl': sl, 'tp': tp, 'date': d, 'tp_dollar': pair_config['tp_dollar']}
    
    elif (e >= row['bu'] - pair_config['bb'] * row['atr'] and 
          row['rsi'] > pair_config['rsi_sell'] and 
          row['sk'] > pair_config['stoch_sell'] and 
          row['sk'] < row['sd']):
        
        sl = e + SL_ATR * row['atr']
        tp = e - tp_atr * row['atr']
        return {'pair': name, 'sig': 'SELL', 'e': e, 'sl': sl, 'tp': tp, 'date': d, 'tp_dollar': pair_config['tp_dollar']}
    
    return None

def check_exit(df, trade):
    row = df.iloc[-1]
    ch = row['high']; cl = row['low']
    fh = row['datetime'].hour
    
    if fh >= SESSION_END:
        if trade['sig'] == 'BUY': return {'out': 'EOD', 'price': cl}
        else: return {'out': 'EOD', 'price': ch}
    
    if trade['sig'] == 'BUY':
        if ch >= trade['tp']: return {'out': 'TP', 'price': trade['tp']}
        if cl <= trade['sl']: return {'out': 'SL', 'price': trade['sl']}
    else:
        if cl <= trade['tp']: return {'out': 'TP', 'price': trade['tp']}
        if ch >= trade['sl']: return {'out': 'SL', 'price': trade['sl']}
    return None

def wait_for_next_candle():
    now = datetime.now(timezone.utc)
    minute = now.minute; second = now.second
    minutes_past = minute % 15
    seconds_past = minutes_past * 60 + second
    wait = (15 * 60) - seconds_past + 45
    if wait < 0: wait = 45
    return wait

# ============ MAIN ============
print("=" * 60)
print("LIVE TRADING BOT - EURUSD | GBPUSD | GOLD")
print(f"Session (UTC): {SESSION_START:02d}:00-{SESSION_END:02d}:00")
print(f"Session (IST): 1:30 PM - 10:30 PM")
print(f"Skip (UTC): {SKIP_HOURS}")
print(f"RR: 1:1.67 Forex | 1:2 GOLD")
print("=" * 60)

send_telegram(f"🚀 <b>TRADING BOT STARTED</b>\n"
              f"📅 {get_ist_time()}\n"
              f"Pairs: EURUSD, GBPUSD, GOLD\n"
              f"Session: 1:30 PM - 10:30 PM IST\n"
              f"Checking every 15 min")

while True:
    try:
        now = datetime.now(timezone.utc)
        d = now.strftime('%Y-%m-%d')
        
        if SESSION_START <= now.hour < SESSION_END and now.hour not in SKIP_HOURS:
            print(f"\n[{get_ist_time_short()}] Checking...")
            
            for pair_config in PAIRS:
                name = pair_config['name']
                df = fetch_data(pair_config['symbol'])
                if df is None or len(df) < 200: continue
                df = calculate_indicators(df)
                
                # Check exit
                if name in active_trades:
                    trade = active_trades[name]
                    exit_result = check_exit(df, trade)
                    
                    if exit_result:
                        out = exit_result['out']
                        price = exit_result['price']
                        entry = trade['e']
                        
                        if trade['sig'] == 'BUY':
                            pips = round((price - entry) * 10000, 1) if name != "GOLD" else round(price - entry, 2)
                        else:
                            pips = round((entry - price) * 10000, 1) if name != "GOLD" else round(entry - price, 2)
                        
                        emoji = "✅" if out == 'TP' else ("🟡" if out == 'EOD' else "❌")
                        profit = trade['tp_dollar'] if out == 'TP' else (0 if out == 'EOD' else -20)
                        
                        msg = (f"{emoji} <b>{name} TRADE CLOSED</b>\n"
                               f"Signal: {trade['sig']}\n"
                               f"Entry: {entry:.5f}\n"
                               f"Exit: {price:.5f}\n"
                               f"SL: {trade['sl']:.5f} | TP: {trade['tp']:.5f}\n"
                               f"Result: <b>{out}</b> | Pips: {pips:+.1f}\n"
                               f"Time: {get_ist_time_short()}\n"
                               f"Date: {get_ist_time()}")
                        send_telegram(msg)
                        print(f"  {emoji} {name} {out} | {trade['sig']} | {entry:.5f} → {price:.5f}")
                        
                        td = trade['date']
                        if td not in daily_trades: daily_trades[td] = {}
                        daily_trades[td][name] = daily_trades[td].get(name, 0) + 1
                        del active_trades[name]
                
                # Check entry
                else:
                    signal = check_entry(df, pair_config)
                    if signal:
                        active_trades[name] = signal
                        direction = "🟢 LONG" if signal['sig'] == 'BUY' else "🔴 SHORT"
                        
                        msg = (f"🔔 <b>NEW {name} SIGNAL</b>\n"
                               f"Direction: {direction}\n"
                               f"Entry: {signal['e']:.5f}\n"
                               f"SL: {signal['sl']:.5f}\n"
                               f"TP: {signal['tp']:.5f}\n"
                               f"Time: {get_ist_time_short()}\n"
                               f"Date: {get_ist_time()}")
                        send_telegram(msg)
                        print(f"  🔔 {name} {signal['sig']} | Entry: {signal['e']:.5f} | SL: {signal['sl']:.5f} | TP: {signal['tp']:.5f}")
        
        # Show active trades
        for name, trade in active_trades.items():
            print(f"  ACTIVE: {name} {trade['sig']} | Entry: {trade['e']:.5f}")
        
        wait = wait_for_next_candle()
        time.sleep(wait)
        
    except KeyboardInterrupt:
        print("\nBot stopped")
        send_telegram(f"🛑 <b>Bot Stopped</b>\n{get_ist_time()}")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(60)