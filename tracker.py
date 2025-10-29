import requests
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from collections import defaultdict
import os
import time  # For retries

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

def fetch_with_retry(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=15)  # Increased timeout
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:  # Rate limit
                wait = 2 ** attempt  # Exponential backoff
                print(f"Rate limited. Retry {attempt+1}/{max_retries} in {wait}s...")
                time.sleep(wait)
                continue
            else:
                print(f"API error {resp.status_code}")
                return []
        except Exception as e:
            print(f"Request failed (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    print("All retries failed — skipping this run")
    return []

def send_email(subject, body):
    if not EMAIL_TO or not GMAIL_USER or not GMAIL_PASS:
        print("Missing email config — skipping send")
        return
    
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    
    recipients = [email.strip() for email in EMAIL_TO.split(",") if email.strip()]
    if not recipients:
        print("No recipients — skipping send")
        return
    msg["To"] = ", ".join(recipients)
    
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.send_message(msg)
        print(f"Email sent to: {', '.join(recipients)}")
    except smtplib.SMTPAuthenticationError:
        print("Gmail auth failed — check App Password (common intermittent issue)")
    except Exception as e:
        print(f"Failed to send email: {e}")

# ========== MAIN ==========
def main():
    print("=== Congress Trade Check Started ===")
    
    # Fetch API with retry
    raw_trades = fetch_with_retry(API_URL)
    if not raw_trades:
        print("No data from API — ending run")
        return
    
    print(f"Fetched {len(raw_trades)} raw trades")
    
    # Load or create trades.json
    try:
        with open("trades.json", "r") as f:
            all_trades = json.load(f)
        print(f"Loaded {len(all_trades)} existing trades")
    except:
        all_trades = []
        print("No trades.json — starting fresh")
    
    seen = {t.get("TransactionID", "") for t in all_trades}  # .get() prevents KeyError
    new_trades = []
    
    for t in raw_trades:
        tid = t.get("TransactionID", "")
        if not tid or tid in seen:
            continue
        try:
            trade_date = datetime.strptime(t["Date"], "%Y-%m-%d")
            report_date = datetime.strptime(t.get("Filed", t["Date"]), "%Y-%m-%d")
            lag = (report_date - trade_date).days
            t["Lag"] = lag
            t["Sector"] = get_sector(t["Ticker"])
            new_trades.append(t)
            all_trades.append(t)
        except Exception as e:
            print(f"Parse error for trade {tid}: {e}")
            continue
    
    if not new_trades:
        print("No new trades — ending run")
        # Still save (in case of parses)
        with open("trades.json", "w") as f:
            json.dump(all_trades, f, indent=2)
        return
    
    # Save updated trades
    try:
        with open("trades.json", "w") as f:
            json.dump(all_trades, f, indent=2)
        print(f"Saved {len(all_trades)} trades total ({len(new_trades)} new)")
    except Exception as e:
        print(f"Save failed: {e}")
        return  # Don't alert if can't save
    
    # ========== ALERTS ==========
    alerts = []
    for t in new_trades:
        try:
            amount_str = t["Range"].split("$")[1].split("-")[0].replace(",", "")
            amount = int(amount_str)
        except:
            amount = 0
            print(f"Amount parse fail for {t.get('Ticker', 'Unknown')}")
        
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
              and t.get('Transaction') == 'Purchase' 
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
        send_email("🚨 Congress Trade Alert", body)
    else:
        print("No alerts triggered")
    
    print("=== Run Complete ===")

if __name__ == "__main__":
    main()
