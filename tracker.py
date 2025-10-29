import requests
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from collections import defaultdict
import os

# ========== CONFIG (change these 3 lines only) ==========
GMAIL_USER = os.getenv("GMAIL_USER")      # you@gmail.com
GMAIL_PASS = os.getenv("GMAIL_PASS")      # App password
EMAIL_TO = os.getenv("EMAIL_TO")          # you@gmail.com or 1234567890@vtext.com

ACTIVE_TRADERS = ["Josh Gottheimer", "David Rouzer", "Mark Green"]
PELOSI_MAX_LAG = 20
MIN_AMOUNT = 50001
MAX_LAG = 30
SECTOR_SURGE = 3

# ========== DON'T TOUCH BELOW HERE ==========
API_URL = "https://api.quiverquant.com/beta/live/congresstrading"

SECTORS = {
    "NVDA|AMD|AVGO|TSM": "Semiconductors",
    "TSLA|LI|NIO|LCID": "EV", 
    "LMT|RTX|NOC|GD": "Defense",
    "XOM|CVX|COP|SHEL": "Energy",
    "JPM|BAC|WFC|C": "Banking",
    "GOOGL|META|AMZN|MSFT|AAPL": "Big Tech",
}

def get_sector(ticker):
    for keys, sector in SECTORS.items():
        if ticker in keys.split("|"):
            return sector
    return "Other"

def send_email(subject, body):
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO
    
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)
    print("✅ Email sent!")

# ========== MAIN LOGIC ==========
def main():
    # Get trades
    print("🔍 Checking for new trades...")
    resp = requests.get(API_URL, timeout=10)
    if resp.status_code != 200:
        print("❌ API error")
        return
    
    raw_trades = resp.json()
    
    # Load old trades
    try:
        with open("trades.json", "r") as f:
            all_trades = json.load(f)
        seen = {t["TransactionID"] for t in all_trades}
    except:
        all_trades = []
        seen = set()
    
    # Find new trades
    new_trades = []
    for t in raw_trades:
        if t["TransactionID"] not in seen:
            # Calculate lag
            trade_date = datetime.strptime(t["Date"], "%Y-%m-%d")
            report_date = datetime.strptime(t.get("Filed", t["Date"]), "%Y-%m-%d")
            lag = (report_date - trade_date).days
            
            t["Lag"] = lag
            t["Sector"] = get_sector(t["Ticker"])
            new_trades.append(t)
            all_trades.append(t)
    
    if not new_trades:
        print("ℹ️  No new trades")
        return
    
    # Save all trades
    with open("trades.json", "w") as f:
        json.dump(all_trades, f, indent=2)
    
    print(f"✅ Found {len(new_trades)} new trades")
    
    # ========== ALERT 1: High-signal trades ==========
    alerts = []
    for t in new_trades:
        amount = int(t["Range"].split("$")[1].split("-")[0].replace(",", ""))
        
        is_pelosi = "Pelosi" in t["Representative"]
        lag_ok = t["Lag"] <= (PELOSI_MAX_LAG if is_pelosi else MAX_LAG)
        
        if (t["Representative"] in ACTIVE_TRADERS or is_pelosi) and \
           t["Transaction"] == "Purchase" and \
           amount >= MIN_AMOUNT and \
           lag_ok:
            
            alert = f"""🚨 HIGH SIGNAL TRADE
👤 {t['Representative']}
📅 {t['Date']} (Lag: {t['Lag']} days)
💰 Purchase {t['Range']}
📈 {t['Ticker']} ({t['Sector']})
🔗 {t['ReportLink']}"""
            alerts.append(alert)
    
    # ========== ALERT 2: Sector surges ==========
    cutoff = datetime.now() - timedelta(days=7)
    recent = [t for t in all_trades 
              if datetime.strptime(t['Date'], '%Y-%m-%d') >= cutoff 
              and t['Transaction'] == 'Purchase' 
              and t['Sector'] != 'Other']
    
    sector_count = defaultdict(int)
    for t in recent:
        sector_count[t['Sector']] += 1
    
    for sector, count in sector_count.items():
        if count >= SECTOR_SURGE:
            buyers = list({t['Representative'] for t in recent if t['Sector'] == sector})
            alert = f"""🌊 SECTOR SURGE: {sector}
🔥 {count} members bought in 7 days
👥 {', '.join(buyers[:5])}"""
            alerts.append(alert)
    
    # Send email
    if alerts:
        body = "\n\n---\n\n".join(alerts)
        send_email("🚨 Congress Trade Alert", body)
        print("🎉 Alerts sent!")
    else:
        print("ℹ️  No alerts triggered")

if __name__ == "__main__":
    main()
