import requests
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from collections import defaultdict
import os
import time

# ========== CONFIG ==========
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_PASS = os.getenv("GMAIL_PASS")
EMAIL_TO   = os.getenv("EMAIL_TO")

ACTIVE_TRADERS  = ["Josh Gottheimer", "David Rouzer", "Mark Green"]
PELOSI_MAX_LAG  = 20
MIN_AMOUNT      = 50001
MAX_LAG         = 30
SECTOR_SURGE    = 3

# Free public data (no API key needed)
HOUSE_URL  = "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
SENATE_URL = "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/transactions.json"

SECTORS = {
    "NVDA|AMD|AVGO|TSM": "Semiconductors",
    "TSLA|LI|NIO|LCID": "EV",
    "LMT|RTX|NOC|GD": "Defense",
    "XOM|CVX|COP|SHEL": "Energy",
    "JPM|BAC|WFC|C": "Banking",
    "GOOGL|META|AMZN|MSFT|AAPL": "Big Tech",
}

def get_sector(ticker):
    if not ticker:
        return "Other"
    ticker = ticker.upper()
    for keys, sector in SECTORS.items():
        if ticker in keys.split("|"):
            return sector
    return "Other"

def fetch_with_retry(url, max_retries=3, timeout=20):
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"Fetch {url} -> HTTP {r.status_code}")
                return []
        except Exception as e:
            wait = 2 ** attempt
            print(f"Fetch failed ({attempt+1}/{max_retries}) {url}: {e} — retrying in {wait}s")
            time.sleep(wait)
    print(f"All retries failed for {url}")
    return []

def normalize_house(item):
    # Typical fields: transaction_date, disclosure_date, ticker, type, amount, representative, link
    try:
        date_str  = item.get("transaction_date") or item.get("transaction_date_original") or item.get("transaction_date_dt")
        filed_str = item.get("disclosure_date") or item.get("filed") or ""
        # Normalize date formats to YYYY-MM-DD
        def norm(d):
            if not d: return ""
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(d[:10], fmt).strftime("%Y-%m-%d")
                except Exception:
                    pass
            return d[:10]
        date  = norm(date_str)
        filed = norm(filed_str) if filed_str else date

        rep   = item.get("representative") or ""
        ticker= (item.get("ticker") or "").upper().strip()
        typ   = (item.get("type") or "").title()  # "Purchase", "Sale", etc.
        rng   = item.get("amount") or ""          # e.g., "$1,001 - $15,000"
        link  = item.get("link") or item.get("ptr_link") or ""
        # Build a reasonably unique id
        tid   = f"H|{rep}|{ticker}|{date}|{filed}|{typ}|{rng}"

        return {
            "Representative": rep,
            "Ticker": ticker or "N/A",
            "Date": date,
            "Filed": filed,
            "Transaction": "Purchase" if "purchase" in typ.lower() else ("Sale" if "sale" in typ.lower() else typ),
            "Range": rng if rng else "$0 - $0",
            "ReportLink": link,
            "TransactionID": tid
        }
    except Exception as e:
        print(f"Normalize house error: {e}")
        return None

def normalize_senate(item):
    # Typical fields: transaction_date, disclosure_date, ticker, type, amount, senator, ptr_link
    try:
        def norm(d):
            if not d: return ""
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(d[:10], fmt).strftime("%Y-%m-%d")
                except Exception:
                    pass
            return d[:10]

        date  = norm(item.get("transaction_date"))
        filed = norm(item.get("disclosure_date")) or date

        rep    = item.get("senator") or ""
        ticker = (item.get("ticker") or "").upper().strip()
        typ    = (item.get("type") or "").title()
        rng    = item.get("amount") or ""
        link   = item.get("ptr_link") or item.get("link") or ""

        tid    = f"S|{rep}|{ticker}|{date}|{filed}|{typ}|{rng}"

        return {
            "Representative": rep,
            "Ticker": ticker or "N/A",
            "Date": date,
            "Filed": filed,
            "Transaction": "Purchase" if "purchase" in typ.lower() else ("Sale" if "sale" in typ.lower() else typ),
            "Range": rng if rng else "$0 - $0",
            "ReportLink": link,
            "TransactionID": tid
        }
    except Exception as e:
        print(f"Normalize senate error: {e}")
        return None

def parse_amount_floor(range_str):
    # Expect formats like "$1,001 - $15,000" or "$50,001 - $100,000"
    try:
        if not range_str or "$" not in range_str:
            return 0
        part = range_str.split("$", 1)[1]  # after first $
        left = part.split("-")[0].strip().replace(",", "")
        return int(left)
    except Exception:
        return 0

def send_email(subject, body):
    if not EMAIL_TO or not GMAIL_USER or not GMAIL_PASS:
        print("Missing email config — skipping send")
        return
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER

    recipients = [e.strip() for e in EMAIL_TO.split(",") if e.strip()]
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
        print("Gmail auth failed — check App Password")
    except Exception as e:
        print(f"Failed to send email: {e}")

# ========== MAIN ==========
def main():
    print("=== Congress Trade Check Started (free mode) ===")

    house_raw  = fetch_with_retry(HOUSE_URL)
    senate_raw = fetch_with_retry(SENATE_URL)

    if not house_raw and not senate_raw:
        print("No data from free sources — ending run")
        return

    # Normalize & combine
    combined = []
    for it in house_raw or []:
        n = normalize_house(it)
        if n: combined.append(n)
    for it in senate_raw or []:
        n = normalize_senate(it)
        if n: combined.append(n)

    print(f"Fetched {len(combined)} normalized trades")

    # Load or create trades.json
    try:
        with open("trades.json", "r") as f:
            all_trades = json.load(f)
        print(f"Loaded {len(all_trades)} existing trades")
    except Exception:
        all_trades = []
        print("No trades.json — starting fresh")

    seen = {t.get("TransactionID", "") for t in all_trades}
    new_trades = []

    for t in combined:
        tid = t.get("TransactionID", "")
        if not tid or tid in seen:
            continue
        try:
            trade_date = datetime.strptime(t["Date"], "%Y-%m-%d")
            report_date = datetime.strptime(t.get("Filed", t["Date"]), "%Y-%m-%d")
            lag = (report_date - trade_date).days
            t["Lag"] = max(lag, 0)
            t["Sector"] = get_sector(t["Ticker"])
            new_trades.append(t)
            all_trades.append(t)
        except Exception as e:
            print(f"Parse error for trade {tid}: {e}")
            continue

    # Save updates even if no alerts (for dedupe next time)
    try:
        with open("trades.json", "w") as f:
            json.dump(all_trades, f, indent=2)
        if new_trades:
            print(f"Saved {len(all_trades)} trades total ({len(new_trades)} new)")
        else:
            print("No new trades — ending run")
    except Exception as e:
        print(f"Save failed: {e}")
        return

    # Alerts
    alerts = []
    for t in new_trades:
        amount_floor = parse_amount_floor(t["Range"])
        is_pelosi = "Pelosi" in t["Representative"]
        lag_ok = t["Lag"] <= (PELOSI_MAX_LAG if is_pelosi else MAX_LAG)

        if (t["Representative"] in ACTIVE_TRADERS or is_pelosi) and \
           t["Transaction"] == "Purchase" and \
           amount_floor >= MIN_AMOUNT and \
           lag_ok:

            alert = f"""HIGH SIGNAL TRADE
{t['Representative']}
{t['Date']} (Lag: {t['Lag']} days)
Purchase {t['Range']}
{t['Ticker']} ({t['Sector']})
{t['ReportLink']}"""
            alerts.append(alert)

    # Sector surge (7 days)
    cutoff = datetime.now() - timedelta(days=7)
    recent = []
    for t in all_trades:
        try:
            if datetime.strptime(t['Date'], '%Y-%m-%d') >= cutoff \
               and t.get('Transaction') == 'Purchase' \
               and t.get('Sector') != 'Other':
                recent.append(t)
        except Exception:
            pass

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
