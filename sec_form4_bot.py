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

def get_filing_details(filing_url):
    """Fetch detailed Form 4 XML data"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        print(f"Fetching filing details from: {filing_url}")
        
        # The filing_url from RSS is like: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=...
        # We need to convert it to get the actual filing
        response = requests.get(filing_url, headers=headers, timeout=10)
        response.raise_for_status()
        time.sleep(0.2)
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the documents table and get the XML file
        xml_link = None
        for table in soup.find_all('table', {'class': 'tableFile'}):
            for row in table.find_all('tr')[1:]:  # Skip header
                cells = row.find_all('td')
                if len(cells) >= 3:
                    doc_type = cells[3].text.strip() if len(cells) > 3 else ''
                    if 'primary_doc.xml' in cells[2].text or (doc_type and 'xml' in doc_type.lower()):
                        link_tag = cells[2].find('a')
                        if link_tag and link_tag.get('href'):
                            xml_link = 'https://www.sec.gov' + link_tag['href']
                            break
            if xml_link:
                break
        
        if not xml_link:
            print("Could not find XML link in filing page")
            return None
        
        print(f"Found XML link: {xml_link}")
        
        # Fetch the XML
        xml_response = requests.get(xml_link, headers=headers, timeout=10)
        xml_response.raise_for_status()
        time.sleep(0.2)
        
        # Parse XML with better namespace handling
        xml_soup = BeautifulSoup(xml_response.content, 'lxml-xml')
        
        details = {}
        
        # Extract issuer information
        issuer = xml_soup.find('issuer')
        if issuer:
            issuer_cik = issuer.find('issuerCik')
            issuer_name = issuer.find('issuerName')
            issuer_symbol = issuer.find('issuerTradingSymbol')
            
            details['issuer_name'] = issuer_name.text.strip() if issuer_name else 'N/A'
            details['ticker'] = issuer_symbol.text.strip() if issuer_symbol else 'N/A'
            details['cik'] = issuer_cik.text.strip() if issuer_cik else 'N/A'
            
            print(f"Issuer: {details['issuer_name']} ({details['ticker']})")
        
        # Extract reporting owner information
        owner = xml_soup.find('reportingOwner')
        if owner:
            owner_id = owner.find('reportingOwnerId')
            if owner_id:
                owner_name = owner_id.find('rptOwnerName')
                if owner_name:
                    details['owner_name'] = owner_name.text.strip()
                    print(f"Owner: {details['owner_name']}")
            
            relationship = owner.find('reportingOwnerRelationship')
            if relationship:
                titles = []
                if relationship.find('isDirector') and relationship.find('isDirector').text.strip() == '1':
                    titles.append('Director')
                if relationship.find('isOfficer') and relationship.find('isOfficer').text.strip() == '1':
                    officer_title = relationship.find('officerTitle')
                    if officer_title and officer_title.text.strip():
                        titles.append(officer_title.text.strip())
                if relationship.find('isTenPercentOwner') and relationship.find('isTenPercentOwner').text.strip() == '1':
                    titles.append('10% Owner')
                if relationship.find('isOther') and relationship.find('isOther').text.strip() == '1':
                    titles.append('Other')
                    
                details['owner_title'] = ', '.join(titles) if titles else 'Beneficial Owner'
                print(f"Title: {details['owner_title']}")
        
        # Extract transaction information
        transactions = []
        
        # Non-derivative transactions (regular stock)
        for non_deriv in xml_soup.find_all('nonDerivativeTransaction'):
            trans = parse_transaction(non_deriv, is_derivative=False)
            if trans:
                transactions.append(trans)
                print(f"Transaction: {trans['type']} - {trans['shares']} shares @ ${trans['price']}")
        
        # Derivative transactions (options, etc.)
        for deriv in xml_soup.find_all('derivativeTransaction'):
            trans = parse_transaction(deriv, is_derivative=True)
            if trans:
                transactions.append(trans)
                print(f"Derivative Transaction: {trans['type']} - {trans['shares']} @ ${trans['price']}")
        
        details['transactions'] = transactions
        
        return details
        
    except Exception as e:
        print(f"Error fetching filing details: {e}")
        import traceback
        traceback.print_exc()
        return None

def parse_transaction(transaction_node, is_derivative=False):
    """Parse a transaction node from XML"""
    trans = {}
    
    try:
        # Security title
        if is_derivative:
            security = transaction_node.find('derivativeSecurityTitle')
        else:
            security = transaction_node.find('securityTitle')
        
        if security:
            value = security.find('value')
            trans['security'] = value.text.strip() if value else 'Common Stock'
        else:
            trans['security'] = 'Common Stock'
        
        # Transaction coding (P=Purchase, S=Sale, A=Award, etc.)
        trans_coding = transaction_node.find('transactionCoding')
        if trans_coding:
            code_node = trans_coding.find('transactionCode')
            if code_node:
                code = code_node.text.strip()
                trans_map = {
                    'P': 'Purchase',
                    'S': 'Sale',
                    'A': 'Award/Grant',
                    'D': 'Disposition',
                    'F': 'Payment of Exercise Price',
                    'I': 'Discretionary Transaction',
                    'M': 'Exercise/Conversion',
                    'G': 'Gift',
                    'W': 'Acquisition (Will)',
                    'C': 'Conversion',
                    'X': 'Exercise of Options'
                }
                trans['type'] = trans_map.get(code, code)
                trans['code'] = code
        
        # Transaction amounts
        amounts = transaction_node.find('transactionAmounts')
        if amounts:
            shares_node = amounts.find('transactionShares')
            if shares_node:
                value = shares_node.find('value')
                trans['shares'] = value.text.strip() if value else '0'
            
            price_node = amounts.find('transactionPricePerShare')
            if price_node:
                value = price_node.find('value')
                trans['price'] = value.text.strip() if value else '0'
            else:
                trans['price'] = '0'
            
            acquired_node = amounts.find('transactionAcquiredDisposedCode')
            if acquired_node:
                value = acquired_node.find('value')
                if value and value.text.strip() == 'D':
                    trans['disposition'] = True
        
        # Calculate dollar amount
        try:
            shares_num = float(trans.get('shares', '0').replace(',', ''))
            price_num = float(trans.get('price', '0').replace(',', ''))
            trans['amount'] = shares_num * price_num
        except:
            trans['amount'] = 0
        
        return trans
        
    except Exception as e:
        print(f"Error parsing transaction: {e}")
        return None

def fetch_form4_filings():
    """Fetch latest Form 4 filings from SEC EDGAR"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0'
    }
    
    try:
        response = requests.get(SEC_RSS_URL, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml-xml')
        entries = soup.find_all('entry')
        
        filings = []
        for entry in entries[:10]:
            title = entry.find('title').text if entry.find('title') else 'N/A'
            link_tag = entry.find('link')
            link = link_tag['href'] if link_tag and link_tag.get('href') else ''
            updated = entry.find('updated').text if entry.find('updated') else ''
            
            filing_info = {
                'title': title,
                'link': link,
                'updated': updated
            }
            filings.append(filing_info)
        
        return filings
    except Exception as e:
        print(f"Error fetching filings: {e}")
        return []

def send_discord_notification(filing, details):
    """Send a Discord notification for a new filing"""
    
    if not details or not details.get('transactions'):
        print("No transaction details available, sending basic notification")
        # Fallback notification
        company = filing['title'].split(' - ')[0] if ' - ' in filing['title'] else 'Unknown Company'
        embed = {
            "title": f"🔔 New Form 4 Filing",
            "description": f"**{company}**\n\n⚠️ Could not parse transaction details. [View Filing]({filing['link']})",
            "url": filing['link'],
            "color": 3447003,
            "footer": {"text": "SEC EDGAR Form 4 Tracker"},
            "timestamp": datetime.utcnow().isoformat()
        }
    else:
        # Rich notification with full details
        issuer_name = details.get('issuer_name', 'N/A')
        ticker = details.get('ticker', 'N/A')
        owner_name = details.get('owner_name', 'N/A')
        owner_title = details.get('owner_title', 'N/A')
        
        fields = [
            {
                "name": "🏢 Company",
                "value": f"**{issuer_name}**",
                "inline": True
            },
            {
                "name": "📈 Ticker",
                "value": f"**{ticker}**",
                "inline": True
            },
            {
                "name": "👤 Insider",
                "value": owner_name,
                "inline": False
            },
            {
                "name": "💼 Title",
                "value": owner_title,
                "inline": False
            }
        ]
        
        # Add transaction details
        transactions = details.get('transactions', [])
        total_value = 0
        
        for i, trans in enumerate(transactions[:5], 1):  # Limit to 5 transactions
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
                    amount_fmt = f"${float(amount):,.2f}"
                else:
                    price_fmt = "N/A"
                    amount_fmt = "N/A"
                
                if amount > 0:
                    total_value += amount
            except:
                shares_fmt = shares
                price_fmt = price if price != '0' else "N/A"
                amount_fmt = "N/A"
            
            # Emoji based on transaction type
            code = trans.get('code', '')
            if code in ['P', 'A', 'M', 'X']:
                emoji = "🟢"
            elif code in ['S', 'D', 'F']:
                emoji = "🔴"
            else:
                emoji = "🔵"
            
            trans_value = f"{emoji} **{trans_type}**\n"
            trans_value += f"Shares: **{shares_fmt}**"
            if price_fmt != "N/A":
                trans_value += f" @ {price_fmt}"
            if amount_fmt != "N/A":
                trans_value += f"\nValue: **{amount_fmt}**"
            trans_value += f"\nSecurity: {security}"
            
            fields.append({
                "name": f"Transaction {i}" if len(transactions) > 1 else "Transaction",
                "value": trans_value,
                "inline": False
            })
        
        # Add total if multiple transactions
        if len(transactions) > 1 and total_value > 0:
            fields.append({
                "name": "💰 Total Transaction Value",
                "value": f"**${total_value:,.2f}**",
                "inline": False
            })
        
        # Determine color based on transaction type
        has_purchase = any(t.get('code') in ['P', 'A', 'M', 'X'] for t in transactions)
        has_sale = any(t.get('code') in ['S', 'D', 'F'] for t in transactions)
        
        if has_purchase and not has_sale:
            color = 5763719  # Green
        elif has_sale and not has_purchase:
            color = 15158332  # Red
        else:
            color = 3447003  # Blue (mixed)
        
        embed = {
            "title": f"📊 Form 4 Filing: {ticker}",
            "url": filing['link'],
            "color": color,
            "fields": fields,
            "footer": {"text": "SEC EDGAR Form 4 Tracker"},
            "timestamp": datetime.utcnow().isoformat()
        }
    
    payload = {"embeds": [embed]}
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        response.raise_for_status()
        ticker = details.get('ticker', 'Unknown') if details else 'Unknown'
        print(f"✓ Notification sent for: {ticker}")
    except Exception as e:
        print(f"✗ Error sending Discord notification: {e}")

def main():
    print(f"\n{'='*60}")
    print(f"SEC Form 4 Tracker - {datetime.now()}")
    print(f"{'='*60}\n")
    
    # Load last seen filings
    last_filings = load_last_filings()
    last_links = set(f['link'] for f in last_filings)
    
    # Fetch current filings
    current_filings = fetch_form4_filings()
    
    if not current_filings:
        print("No filings fetched. Exiting.")
        return
    
    print(f"Found {len(current_filings)} total filings")
    
    # Find new filings
    new_filings = [f for f in current_filings if f['link'] not in last_links]
    
    if new_filings:
        print(f"\n🆕 Found {len(new_filings)} new filings:\n")
        for filing in reversed(new_filings):  # Process oldest first
            print(f"Processing: {filing['title'][:80]}...")
            details = get_filing_details(filing['link'])
            send_discord_notification(filing, details)
            time.sleep(2)  # Rate limit between notifications
            print()
    else:
        print("No new filings found")
    
    # Save current state
    save_last_filings(current_filings)
    print("\n✓ State saved successfully")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()