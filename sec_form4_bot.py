import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import os
import re
import time

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1426466503644942337/r5mW7kh2XlAJxbjOY7vUCRYi1g5Lt8mrrVbdRr4xlZe3JpFlXfknyYMtIGNUTKDUCNkO"
STATE_FILE = "last_filings.json"
SEC_RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb=&owner=include&start=0&count=40&output=atom"

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

def get_text(element, default=''):
    """Safely extract text from XML element"""
    if element is None:
        return default
    text = element.get_text(strip=True)
    return text if text else default

def get_filing_details(filing_url):
    """Fetch detailed Form 4 XML data"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0',
        'Accept': '*/*'
    }
    
    try:
        print(f"Fetching: {filing_url}")
        
        # Get the filing page
        response = requests.get(filing_url, headers=headers, timeout=15)
        response.raise_for_status()
        time.sleep(0.2)
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the XML document - it's usually called something like "primary_doc.xml" or just has .xml extension
        xml_link = None
        
        # Look in the documents table
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) >= 3:
                    # Check if this row contains a document link
                    link_cell = cells[2] if len(cells) > 2 else cells[1]
                    link = link_cell.find('a')
                    if link and link.get('href'):
                        href = link['href']
                        # Look for .xml files (but not the XSL stylesheet)
                        if '.xml' in href and 'xsl' not in href.lower():
                            xml_link = 'https://www.sec.gov' + href
                            break
            if xml_link:
                break
        
        if not xml_link:
            print("  ✗ Could not find XML document")
            return None
        
        print(f"  Found XML: {xml_link.split('/')[-1]}")
        
        # Fetch the actual XML
        xml_response = requests.get(xml_link, headers=headers, timeout=15)
        xml_response.raise_for_status()
        time.sleep(0.2)
        
        # Parse with xml parser for proper namespace handling
        xml_soup = BeautifulSoup(xml_response.content, 'xml')
        
        details = {}
        
        # Find the ownershipDocument root or work with what we have
        doc = xml_soup.find('ownershipDocument') or xml_soup
        
        # Extract issuer information
        issuer = doc.find('issuer')
        if issuer:
            details['issuer_name'] = get_text(issuer.find('issuerName'), 'N/A')
            details['ticker'] = get_text(issuer.find('issuerTradingSymbol'), 'N/A')
            details['cik'] = get_text(issuer.find('issuerCik'), 'N/A')
            print(f"  Issuer: {details['issuer_name']} ({details['ticker']})")
        else:
            print("  ✗ No issuer information found")
            return None
        
        # Extract reporting owner
        reporting_owner = doc.find('reportingOwner')
        if reporting_owner:
            owner_id = reporting_owner.find('reportingOwnerId')
            if owner_id:
                details['owner_name'] = get_text(owner_id.find('rptOwnerName'), 'N/A')
                print(f"  Owner: {details['owner_name']}")
            
            # Get relationship/title
            relationship = reporting_owner.find('reportingOwnerRelationship')
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
                if get_text(relationship.find('isOther')) == '1':
                    titles.append('Other')
                
                details['owner_title'] = ', '.join(titles) if titles else 'Beneficial Owner'
                print(f"  Title: {details['owner_title']}")
        
        # Parse transactions
        transactions = []
        
        # Non-derivative transactions (regular stock trades)
        for trans_elem in doc.find_all('nonDerivativeTransaction'):
            trans = parse_non_derivative_transaction(trans_elem)
            if trans:
                transactions.append(trans)
                shares = trans.get('shares', '0')
                price = trans.get('price', '0')
                print(f"  Transaction: {trans['type']} - {shares} shares @ ${price}")
        
        # Derivative transactions (options, warrants, etc.)
        for trans_elem in doc.find_all('derivativeTransaction'):
            trans = parse_derivative_transaction(trans_elem)
            if trans:
                transactions.append(trans)
                shares = trans.get('shares', '0')
                price = trans.get('price', '0')
                print(f"  Derivative: {trans['type']} - {shares} @ ${price}")
        
        details['transactions'] = transactions
        print(f"  ✓ Found {len(transactions)} transaction(s)")
        
        return details if transactions else None
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return None

def parse_non_derivative_transaction(trans_elem):
    """Parse a non-derivative transaction"""
    trans = {}
    
    try:
        # Security title
        security = trans_elem.find('securityTitle')
        trans['security'] = get_text(security.find('value'), 'Common Stock') if security else 'Common Stock'
        
        # Transaction date
        trans_date = trans_elem.find('transactionDate')
        if trans_date:
            trans['date'] = get_text(trans_date.find('value'))
        
        # Transaction coding
        trans_coding = trans_elem.find('transactionCoding')
        if trans_coding:
            code = get_text(trans_coding.find('transactionCode'))
            trans_map = {
                'P': 'Purchase',
                'S': 'Sale', 
                'A': 'Grant/Award',
                'D': 'Disposition',
                'F': 'Payment',
                'I': 'Discretionary',
                'M': 'Exercise',
                'G': 'Gift',
                'W': 'Inheritance'
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
            
            # Check if acquired or disposed
            acq_disp = amounts.find('transactionAcquiredDisposedCode')
            if acq_disp:
                trans['acquired_disposed'] = get_text(acq_disp.find('value'))
        
        # Calculate dollar amount
        try:
            shares_num = float(trans.get('shares', '0').replace(',', ''))
            price_num = float(trans.get('price', '0').replace(',', ''))
            trans['amount'] = shares_num * price_num
        except:
            trans['amount'] = 0
        
        # Post-transaction shares owned
        post_trans = trans_elem.find('postTransactionAmounts')
        if post_trans:
            shares_owned = post_trans.find('sharesOwnedFollowingTransaction')
            if shares_owned:
                trans['shares_owned_after'] = get_text(shares_owned.find('value'), '0')
        
        return trans
        
    except Exception as e:
        print(f"    Error parsing transaction: {e}")
        return None

def parse_derivative_transaction(trans_elem):
    """Parse a derivative transaction (options, warrants, etc.)"""
    trans = {}
    trans['is_derivative'] = True
    
    try:
        # Security title
        security = trans_elem.find('securityTitle')
        trans['security'] = get_text(security.find('value'), 'Derivative') if security else 'Derivative'
        
        # Transaction date
        trans_date = trans_elem.find('transactionDate')
        if trans_date:
            trans['date'] = get_text(trans_date.find('value'))
        
        # Transaction coding
        trans_coding = trans_elem.find('transactionCoding')
        if trans_coding:
            code = get_text(trans_coding.find('transactionCode'))
            trans_map = {
                'P': 'Purchase',
                'S': 'Sale',
                'A': 'Grant/Award',
                'D': 'Disposition',
                'M': 'Exercise',
                'X': 'Exercise'
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
        
        # Exercise/conversion price
        exercise_date = trans_elem.find('exerciseDate')
        if exercise_date:
            trans['exercise_date'] = get_text(exercise_date.find('value'))
        
        # Underlying security
        underlying = trans_elem.find('underlyingSecurity')
        if underlying:
            underlying_title = underlying.find('underlyingSecurityTitle')
            if underlying_title:
                trans['underlying'] = get_text(underlying_title.find('value'))
        
        # Calculate amount
        try:
            shares_num = float(trans.get('shares', '0').replace(',', ''))
            price_num = float(trans.get('price', '0').replace(',', ''))
            trans['amount'] = shares_num * price_num
        except:
            trans['amount'] = 0
        
        return trans
        
    except Exception as e:
        print(f"    Error parsing derivative: {e}")
        return None

def fetch_form4_filings():
    """Fetch latest Form 4 filings from SEC EDGAR RSS feed"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0'
    }
    
    try:
        response = requests.get(SEC_RSS_URL, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'xml')
        entries = soup.find_all('entry')
        
        filings = []
        for entry in entries[:10]:
            title = get_text(entry.find('title'), 'N/A')
            link_elem = entry.find('link')
            link = link_elem.get('href', '') if link_elem else ''
            updated = get_text(entry.find('updated'))
            
            filings.append({
                'title': title,
                'link': link,
                'updated': updated
            })
        
        return filings
        
    except Exception as e:
        print(f"Error fetching RSS feed: {e}")
        return []

def send_discord_notification(filing, details):
    """Send Discord notification with transaction details"""
    
    if not details or not details.get('transactions'):
        # Fallback for failed parsing
        company = filing['title'].split(' - ')[0] if ' - ' in filing['title'] else 'Form 4 Filing'
        embed = {
            "title": "🔔 New Form 4 Filing",
            "description": f"**{company}**\n\n[View Filing]({filing['link']})",
            "url": filing['link'],
            "color": 3447003,
            "footer": {"text": "SEC EDGAR Form 4 Tracker"},
            "timestamp": datetime.utcnow().isoformat()
        }
    else:
        # Rich embed with full details
        issuer = details.get('issuer_name', 'N/A')
        ticker = details.get('ticker', 'N/A')
        owner = details.get('owner_name', 'N/A')
        title = details.get('owner_title', 'N/A')
        
        fields = [
            {"name": "🏢 Company", "value": f"**{issuer}**", "inline": True},
            {"name": "📈 Ticker", "value": f"**{ticker}**", "inline": True},
            {"name": "👤 Insider", "value": owner, "inline": False},
            {"name": "💼 Title", "value": title, "inline": False}
        ]
        
        # Add transactions
        transactions = details.get('transactions', [])
        total_value = 0
        
        for i, trans in enumerate(transactions[:5], 1):
            trans_type = trans.get('type', 'N/A')
            shares = trans.get('shares', '0')
            price = trans.get('price', '0')
            amount = trans.get('amount', 0)
            security = trans.get('security', 'Common Stock')
            
            # Format numbers
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
            
            # Emoji
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
        
        # Color based on transaction types
        has_buy = any(t.get('code') in ['P', 'A', 'M', 'X'] for t in transactions)
        has_sell = any(t.get('code') in ['S', 'D', 'F'] for t in transactions)
        color = 5763719 if has_buy and not has_sell else 15158332 if has_sell else 3447003
        
        embed = {
            "title": f"📊 Form 4: {ticker}",
            "url": filing['link'],
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

def main():
    print(f"\n{'='*70}")
    print(f"SEC Form 4 Tracker - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")
    
    last_filings = load_last_filings()
    last_links = set(f['link'] for f in last_filings)
    
    current_filings = fetch_form4_filings()
    
    if not current_filings:
        print("No filings fetched. Exiting.\n")
        return
    
    print(f"Found {len(current_filings)} total filings in RSS feed")
    
    new_filings = [f for f in current_filings if f['link'] not in last_links]
    
    if new_filings:
        print(f"\n🆕 Processing {len(new_filings)} new filing(s):\n")
        for filing in reversed(new_filings):
            title_short = filing['title'][:70] + '...' if len(filing['title']) > 70 else filing['title']
            print(f"📄 {title_short}")
            
            details = get_filing_details(filing['link'])
            send_discord_notification(filing, details)
            print()
            time.sleep(2)
    else:
        print("No new filings to process")
    
    save_last_filings(current_filings)
    print(f"✓ State saved\n{'='*70}\n")

if __name__ == "__main__":
    main()