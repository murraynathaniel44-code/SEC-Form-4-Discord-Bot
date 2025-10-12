import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta
import os
import time
import sys

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1427024017126195281/XsX8beOMl7mQajGBCkCFEPPrbtWaAENxb2pCwe83GHwAZpDEw5x29nXZDu_BB1PmOv3p"
STATE_FILE = "last_filings.json"
FILTERS_FILE = "ticker_filters.json"

# Official SEC EDGAR RSS feed
SEC_DAILY_INDEX_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"

def load_last_filings():
    """Load the last seen filings from state file"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return []

def save_last_filings(filings):
    """Save the current filings to state file"""
    with open(STATE_FILE, 'w') as f:
        json.dump(filings, f)

def load_ticker_filters():
    """Load ticker filters from file"""
    if os.path.exists(FILTERS_FILE):
        with open(FILTERS_FILE, 'r') as f:
            data = json.load(f)
            return set(ticker.upper() for ticker in data.get('tickers', []))
    return set()

def save_ticker_filters(tickers):
    """Save ticker filters to file"""
    with open(FILTERS_FILE, 'w') as f:
        json.dump({'tickers': sorted(list(tickers))}, f, indent=2)

def add_ticker_filter(ticker):
    """Add a ticker to the filter list"""
    filters = load_ticker_filters()
    ticker = ticker.upper()
    filters.add(ticker)
    save_ticker_filters(filters)
    return filters

def remove_ticker_filter(ticker):
    """Remove a ticker from the filter list"""
    filters = load_ticker_filters()
    ticker = ticker.upper()
    filters.discard(ticker)
    save_ticker_filters(filters)
    return filters

def clear_ticker_filters():
    """Clear all ticker filters"""
    save_ticker_filters(set())
    return set()

def send_filters_notification():
    """Send a notification showing active filters"""
    filters = load_ticker_filters()
    
    if filters:
        ticker_list = ', '.join(f"**{ticker}**" for ticker in sorted(filters))
        description = f"Currently monitoring {len(filters)} ticker(s):\n\n{ticker_list}"
        color = 3447003  # Blue
    else:
        description = "No ticker filters active. Monitoring **all** Form 4 filings."
        color = 10197915  # Gray
    
    embed = {
        "title": "📋 Active Ticker Filters",
        "description": description,
        "color": color,
        "footer": {"text": "Use ticker_filters.json to manage filters"},
        "timestamp": datetime.utcnow().isoformat()
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        response.raise_for_status()
        print("✓ Filters notification sent")
    except Exception as e:
        print(f"✗ Error sending filters notification: {e}")

def get_text(element, default=''):
    """Safely extract text from XML element"""
    if element is None:
        return default
    text = element.get_text(strip=True)
    return text if text else default

def extract_ticker_from_title(title):
    """Extract ticker symbol from filing title if present"""
    # Title format is usually: "Company Name (TICKER) - Form Type - Accession Number"
    import re
    match = re.search(r'\(([A-Z]+)\)', title)
    if match:
        return match.group(1).upper()
    return None

def fetch_latest_form4_filings(ticker_filters=None):
    """Fetch the latest Form 4 filings from SEC EDGAR using the official RSS feed
    
    Args:
        ticker_filters: Set of ticker symbols to filter for. If None or empty, returns all filings.
    """
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0 (contact@example.com)',
        'Accept': 'application/atom+xml,application/xml,text/xml,*/*',
        'Accept-Encoding': 'gzip, deflate'
    }
    
    # This RSS feed is the official SEC source for latest Form 4 filings
    rss_url = f"{SEC_DAILY_INDEX_BASE}?action=getcurrent&type=4&company=&dateb=&owner=include&start=0&count=100&output=atom"
    
    try:
        if ticker_filters:
            print(f"Fetching Form 4 filings for tickers: {', '.join(sorted(ticker_filters))}...")
        else:
            print("Fetching latest Form 4 filings from SEC EDGAR...")
        
        response = requests.get(rss_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'xml')
        entries = soup.find_all('entry')
        
        filings = []
        filtered_count = 0
        
        for entry in entries:
            # Extract filing information
            title = get_text(entry.find('title'))
            
            # Early filtering based on ticker in title
            ticker_in_title = extract_ticker_from_title(title)
            
            # If we have filters and the ticker doesn't match, skip this filing entirely
            if ticker_filters and ticker_in_title:
                if ticker_in_title not in ticker_filters:
                    filtered_count += 1
                    continue
            
            # Get the filing link
            link = entry.find('link')
            filing_url = link.get('href') if link else None
            
            # Get the filing date/time
            updated = get_text(entry.find('updated'))
            
            # Get the summary which contains additional details
            summary = get_text(entry.find('summary'))
            
            if filing_url:
                filings.append({
                    'title': title,
                    'filing_url': filing_url,
                    'filing_date': updated,
                    'summary': summary,
                    'ticker_hint': ticker_in_title  # Store the ticker we found in title
                })
        
        if ticker_filters:
            print(f"  Found {len(filings)} matching filings (filtered out {filtered_count})")
        else:
            print(f"  Found {len(filings)} Form 4 filings")
        
        return filings[:50]  # Return top 50 most recent matching filings
        
    except Exception as e:
        print(f"Error fetching filings: {e}")
        import traceback
        traceback.print_exc()
        return []

def get_filing_xml_url(filing_url):
    """Extract the XML document URL from a filing page"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0 (contact@example.com)',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        response = requests.get(filing_url, headers=headers, timeout=15)
        response.raise_for_status()
        time.sleep(0.15)  # Respect SEC rate limits (10 req/sec = 100ms, we use 150ms to be safe)
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the XML file in the documents table
        for table in soup.find_all('table', class_='tableFile'):
            for row in table.find_all('tr')[1:]:  # Skip header
                cells = row.find_all('td')
                if len(cells) >= 3:
                    doc_link = cells[2].find('a')
                    if doc_link:
                        href = doc_link.get('href', '')
                        # Look for .xml but not .xsl
                        if '.xml' in href and 'xsl' not in href.lower():
                            return 'https://www.sec.gov' + href
        
        return None
        
    except Exception as e:
        print(f"  Error getting XML URL: {e}")
        return None

def parse_form4_xml(xml_url):
    """Parse Form 4 XML and extract transaction details"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0 (contact@example.com)',
        'Accept': 'application/xml,text/xml,*/*'
    }
    
    try:
        print(f"  Parsing XML: {xml_url.split('/')[-1]}")
        response = requests.get(xml_url, headers=headers, timeout=15)
        response.raise_for_status()
        time.sleep(0.15)  # Respect SEC rate limits
        
        soup = BeautifulSoup(response.content, 'xml')
        
        details = {}
        
        # Issuer information
        issuer = soup.find('issuer')
        if issuer:
            details['issuer_name'] = get_text(issuer.find('issuerName'), 'N/A')
            details['ticker'] = get_text(issuer.find('issuerTradingSymbol'), 'N/A')
            details['cik'] = get_text(issuer.find('issuerCik'), 'N/A')
        
        # Reporting owner
        owner = soup.find('reportingOwner')
        if owner:
            owner_id = owner.find('reportingOwnerId')
            if owner_id:
                details['owner_name'] = get_text(owner_id.find('rptOwnerName'), 'N/A')
            
            relationship = owner.find('reportingOwnerRelationship')
            if relationship:
                titles = []
                if get_text(relationship.find('isDirector')) == '1':
                    titles.append('Director')
                if get_text(relationship.find('isOfficer')) == '1':
                    title = get_text(relationship.find('officerTitle'))
                    if title:
                        titles.append(title)
                if get_text(relationship.find('isTenPercentOwner')) == '1':
                    titles.append('10% Owner')
                
                details['owner_title'] = ', '.join(titles) if titles else 'Beneficial Owner'
        
        # Transactions
        transactions = []
        
        # Non-derivative transactions
        for trans_elem in soup.find_all('nonDerivativeTransaction'):
            trans = parse_transaction(trans_elem, is_derivative=False)
            if trans:
                transactions.append(trans)
        
        # Derivative transactions
        for trans_elem in soup.find_all('derivativeTransaction'):
            trans = parse_transaction(trans_elem, is_derivative=True)
            if trans:
                transactions.append(trans)
        
        details['transactions'] = transactions
        
        print(f"    Issuer: {details.get('issuer_name', 'N/A')} ({details.get('ticker', 'N/A')})")
        print(f"    Owner: {details.get('owner_name', 'N/A')}")
        print(f"    Transactions: {len(transactions)}")
        
        return details
        
    except Exception as e:
        print(f"  Error parsing XML: {e}")
        import traceback
        traceback.print_exc()
        return None

def parse_transaction(trans_elem, is_derivative=False):
    """Parse a transaction element"""
    trans = {'is_derivative': is_derivative}
    
    try:
        # Security title
        if is_derivative:
            security = trans_elem.find('derivativeSecurityTitle')
        else:
            security = trans_elem.find('securityTitle')
        
        if security:
            trans['security'] = get_text(security.find('value'), 'Common Stock')
        else:
            trans['security'] = 'Common Stock'
        
        # Transaction date
        trans_date = trans_elem.find('transactionDate')
        if trans_date:
            trans['date'] = get_text(trans_date.find('value'))
        
        # Transaction code
        trans_coding = trans_elem.find('transactionCoding')
        if trans_coding:
            code = get_text(trans_coding.find('transactionCode'))
            trans_map = {
                'P': 'Purchase',
                'S': 'Sale',
                'A': 'Grant/Award',
                'D': 'Disposition',
                'F': 'Payment',
                'M': 'Exercise',
                'G': 'Gift',
                'J': 'Other',
                'K': 'Transaction in Equity Swap'
            }
            trans['type'] = trans_map.get(code, code)
            trans['code'] = code
        
        # Transaction amounts
        amounts = trans_elem.find('transactionAmounts')
        if amounts:
            shares_elem = amounts.find('transactionShares')
            trans['shares'] = get_text(shares_elem.find('value'), '0') if shares_elem else '0'
            
            price_elem = amounts.find('transactionPricePerShare')
            trans['price'] = get_text(price_elem.find('value'), '0') if price_elem else '0'
        
        # Calculate dollar amount
        try:
            shares_num = float(trans.get('shares', '0').replace(',', ''))
            price_num = float(trans.get('price', '0').replace(',', ''))
            trans['amount'] = shares_num * price_num
        except:
            trans['amount'] = 0
        
        return trans
        
    except Exception as e:
        print(f"    Error parsing transaction: {e}")
        return None

def send_discord_notification(filing, details):
    """Send Discord notification"""
    
    # Format the filing date
    filing_date = filing.get('filing_date', '')
    if filing_date:
        try:
            # Parse ISO format date (2025-10-12T21:59:46-04:00)
            date_obj = datetime.fromisoformat(filing_date.replace('Z', '+00:00'))
            filing_date_fmt = date_obj.strftime('%B %d, %Y at %I:%M %p %Z')
        except:
            filing_date_fmt = filing_date
    else:
        filing_date_fmt = 'N/A'
    
    if not details or not details.get('transactions'):
        # Basic notification
        company = filing.get('title', 'Unknown Company').split(' - ')[0]
        embed = {
            "title": "🔔 New Form 4 Filing",
            "description": f"**{company}**\n📅 Filed: {filing_date_fmt}\n\n[View Filing]({filing['filing_url']})",
            "url": filing['filing_url'],
            "color": 3447003,
            "footer": {"text": "SEC EDGAR Form 4 Tracker"},
            "timestamp": datetime.utcnow().isoformat()
        }
    else:
        # Rich notification
        issuer = details.get('issuer_name', 'N/A')
        ticker = details.get('ticker', 'N/A')
        owner = details.get('owner_name', 'N/A')
        title = details.get('owner_title', 'N/A')
        
        fields = [
            {"name": "🏢 Company", "value": f"**{issuer}**", "inline": True},
            {"name": "📈 Ticker", "value": f"**{ticker}**", "inline": True},
            {"name": "📅 Filing Date", "value": filing_date_fmt, "inline": False},
            {"name": "👤 Insider", "value": owner, "inline": False},
            {"name": "💼 Title", "value": title, "inline": False}
        ]
        
        transactions = details.get('transactions', [])
        total_value = 0
        
        for i, trans in enumerate(transactions[:5], 1):
            trans_type = trans.get('type', 'N/A')
            shares = trans.get('shares', '0')
            price = trans.get('price', '0')
            amount = trans.get('amount', 0)
            security = trans.get('security', 'Common Stock')
            
            try:
                shares_fmt = f"{float(shares):,.0f}"
                if float(price) > 0:
                    price_fmt = f"${float(price):,.2f}"
                    amount_fmt = f"${amount:,.2f}"
                else:
                    price_fmt = "N/A"
                    amount_fmt = "N/A"
                
                if amount > 0:
                    total_value += amount
            except:
                shares_fmt = shares
                price_fmt = "N/A"
                amount_fmt = "N/A"
            
            code = trans.get('code', '')
            if code in ['P', 'A', 'M', 'X']:
                emoji = "🟢"
            elif code in ['S', 'D', 'F']:
                emoji = "🔴"
            else:
                emoji = "🔵"
            
            value_text = f"{emoji} **{trans_type}**\n"
            value_text += f"Shares: **{shares_fmt}**"
            if price_fmt != "N/A":
                value_text += f" @ {price_fmt}"
            if amount_fmt != "N/A":
                value_text += f"\nValue: **{amount_fmt}**"
            if trans.get('is_derivative'):
                value_text += f"\nType: Derivative ({security})"
            else:
                value_text += f"\nSecurity: {security}"
            
            fields.append({
                "name": f"Transaction {i}" if len(transactions) > 1 else "Transaction",
                "value": value_text,
                "inline": False
            })
        
        if len(transactions) > 1 and total_value > 0:
            fields.append({
                "name": "💰 Total Value",
                "value": f"**${total_value:,.2f}**",
                "inline": False
            })
        
        has_buy = any(t.get('code') in ['P', 'A', 'M', 'X'] for t in transactions)
        has_sell = any(t.get('code') in ['S', 'D', 'F'] for t in transactions)
        color = 5763719 if has_buy and not has_sell else 15158332 if has_sell else 3447003
        
        embed = {
            "title": f"📊 Form 4: {ticker}",
            "url": filing['filing_url'],
            "color": color,
            "fields": fields,
            "footer": {"text": "SEC EDGAR Form 4 Tracker"},
            "timestamp": datetime.utcnow().isoformat()
        }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        response.raise_for_status()
        print(f"  ✓ Discord notification sent")
    except Exception as e:
        print(f"  ✗ Discord error: {e}")

def should_notify_filing(details, ticker_filters):
    """Check if a filing should generate a notification based on filters
    
    This is a secondary check in case ticker wasn't in the title.
    """
    # If no filters, notify everything
    if not ticker_filters:
        return True
    
    # If filters exist, only notify if ticker matches
    ticker = details.get('ticker', 'N/A').upper() if details else 'N/A'
    return ticker in ticker_filters

def main():
    print(f"\n{'='*70}")
    print(f"SEC Form 4 Tracker - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")
    
    # Check for command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == 'filters' or command == 'tickers':
            send_filters_notification()
            return
        elif command == 'add' and len(sys.argv) > 2:
            ticker = sys.argv[2].upper()
            filters = add_ticker_filter(ticker)
            print(f"✓ Added {ticker} to filters")
            print(f"Active filters: {', '.join(sorted(filters))}")
            send_filters_notification()
            return
        elif command == 'remove' and len(sys.argv) > 2:
            ticker = sys.argv[2].upper()
            filters = remove_ticker_filter(ticker)
            print(f"✓ Removed {ticker} from filters")
            print(f"Active filters: {', '.join(sorted(filters)) if filters else 'None'}")
            send_filters_notification()
            return
        elif command == 'clear':
            clear_ticker_filters()
            print("✓ Cleared all filters")
            send_filters_notification()
            return
    
    # Normal operation - check for filings
    filters = load_ticker_filters()
    if filters:
        print(f"📋 Active ticker filters: {', '.join(sorted(filters))}\n")
    else:
        print("📋 No filters active - monitoring all tickers\n")
    
    # Load last seen filings
    last_filings = load_last_filings()
    last_urls = set(f.get('filing_url') for f in last_filings if f.get('filing_url'))
    
    # Fetch current filings from official SEC RSS feed WITH FILTERING
    current_filings = fetch_latest_form4_filings(ticker_filters=filters)
    
    if not current_filings:
        print("No filings fetched. Exiting.\n")
        return
    
    # Find new filings
    new_filings = [f for f in current_filings if f.get('filing_url') not in last_urls]
    
    if new_filings:
        print(f"\n🆕 Found {len(new_filings)} new filing(s)\n")
        notified_count = 0
        skipped_count = 0
        
        for filing in reversed(new_filings[:10]):  # Process up to 10 new filings, oldest first
            title = filing.get('title', 'Unknown')
            title_short = title[:65] + '...' if len(title) > 65 else title
            print(f"📄 {title_short}")
            
            # Get XML URL
            xml_url = get_filing_xml_url(filing['filing_url'])
            
            if xml_url:
                # Parse the XML
                details = parse_form4_xml(xml_url)
                
                # Secondary check if ticker wasn't in title (rare cases)
                if should_notify_filing(details, filters):
                    send_discord_notification(filing, details)
                    notified_count += 1
                else:
                    ticker = details.get('ticker', 'N/A') if details else 'N/A'
                    print(f"  ⊝ Skipped (ticker {ticker} not in filter list)")
                    skipped_count += 1
            else:
                print(f"  ✗ Could not find XML document")
                if not filters:  # Only notify for parsing failures if no filters
                    send_discord_notification(filing, None)
                    notified_count += 1
            
            print()
            time.sleep(0.5)  # Brief pause between notifications
        
        print(f"✓ Sent {notified_count} notification(s), skipped {skipped_count}")
    else:
        print("No new filings to process")
    
    # Save state
    save_last_filings(current_filings)
    print(f"✓ State saved\n{'='*70}\n")

if __name__ == "__main__":
    main()