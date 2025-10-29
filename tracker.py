import requests
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from collections import defaultdict
import os

# ========== CONFIG ==========
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO")

ACTIVE_TRADERS = ["Josh Gottheimer", "David Rouzer", "Mark Green"]
PELOSI_MAX_LAG = 20
MIN_AMOUNT = 50001
MAX_LAG = 30
SECTOR_SURGE = 3

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
    if not EMAIL_TO or not GMAIL_USER or not GMAIL_PASS:
        print("Missing email config!")
        return
    
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    
    recipients = [email.strip() for email in EMAIL_TO.split(",") if email.strip()]
    if not recipients:
        print("No recipients!")
        return
    msg["To"] = ", ".join(recipients)
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        print(f"Email sent to: {', '.join(recipients)}")
    except Exception as e:
        print(f"Failed to send email: {e}")

# ========== MAIN ==========
def main():
    print("Checking for new trades...")
    
    # Fetch API
    try:
        resp = requests.get(API_URL, timeout=10)
        if resp.status_code != 200:
            print(f"API error: {resp.status_code}")
            return
        raw_trades = resp.json()
    except Exception as e:
        print(f"Request failed: {e}")
        return
    
    # Load or create trades.json
    try:
        with open("trades.json", "r") as f:
            all_trades = json.load(f)
        print(f"Loaded {len(all_trades)} existing trades")
    except:
        all_trades = []
        print("No trades.json found — starting fresh")
    
    seen = {t["TransactionID"] for t in all_trades}
    new_trades = []
    
    for t in raw_trades:
        if t["TransactionID"] not in seen:
            trade_date = datetime.strptime(t["Date"], "%Y-%m-%d")
            report_date = datetime.strptime(t.get("Filed", t["Date"]), "%Y-%m-%d")
            lag = (report_date - trade_date).days
            t["Lag"] = lag
            t["Sector"] = get_sector(t["Ticker"])
            new_trades.append(t)
            all_trades.append(t)
    
    if not new_trades:
        print("No new trades")
        return
    
    # Save updated trades
    try:
        with open("trades.json", "w") as f:
            json.dump(all_trades, f, indent=2)
        print(f"Saved {len(all_trades)} trades")
    except Exception as e:
        print(f"Save failed: {e}")
    
    # ========== ALERTS ==========
    alerts = []
    for t in new_trades:
        try:
            amount = int(t["Range"].split("$")[1].split("-")[0].replace(",", ""))
        except:
            amount = 0
        
        is_pelosi = "Pelosi" in t["Representative"]
        lag_ok = t["Lag"] <= (PELOSI_MAX_LAG if is_pelosi else MAX_LAG)
        
        if (t["Representative"] in ACTIVE_TRADERS or is_pelosi) and \
           t["Transaction"] == "Purchase" and \
           amount >= MIN_AMOUNT and \
           lag_ok:
            
            alert = f"""HIGH SIGNAL TRADE
{t['Representative']}
{t['Date']} (Lag: {t['Lag']} days)
Purchase {t['Range']}
{t['Ticker']} ({t['Sector']})
{t['ReportLink']}"""
            alerts.append(alert)
    
    # Sector surge
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
            alert = f"""SECTOR SURGE: {sector}
{count} members bought in 7 days
{', '.join(buyers[:5])}"""
            alerts.append(alert)
    
    if alerts:
        body = "\n\n---\n\n".join(alerts)
        send_email("Congress Trade Alert", body)
    else:
        print("No alerts triggered")

if __name__ == "__main__":
    main()
