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
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0'
    }
    
    try:
        # Get the filing page
        response = requests.get(filing_url, headers=headers)
        response.raise_for_status()
        time.sleep(0.1)  # Be respectful to SEC servers
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the XML document link
        xml_link = None
        for row in soup.find_all('tr'):
            cells = row.find_all('td')
            if len(cells) >= 3 and 'xml' in cells[2].text.lower():
                link = cells[2].find('a')
                if link:
                    xml_link = 'https://www.sec.gov' + link['href']
                    break
        
        if not xml_link:
            return None
        
        # Fetch the XML
        xml_response = requests.get(xml_link, headers=headers)
        xml_response.raise_for_status()
        time.sleep(0.1)
        
        xml_soup = BeautifulSoup(xml_response.content, 'xml')
        
        # Extract details
        details = {}
        
        # Issuer information
        issuer = xml_soup.find('issuer')
        if issuer:
            details['issuer_name'] = issuer.find('issuerName').text if issuer.find('issuerName') else 'N/A'
            details['ticker'] = issuer.find('issuerTradingSymbol').text if issuer.find('issuerTradingSymbol') else 'N/A'
        
        # Reporting owner information
        owner = xml_soup.find('reportingOwner')
        if owner:
            owner_name = owner.find('rptOwnerName')
            if owner_name:
                details['owner_name'] = owner_name.text
            
            relationship = owner.find('reportingOwnerRelationship')
            if relationship:
                titles = []
                if relationship.find('isDirector') and relationship.find('isDirector').text == '1':
                    titles.append('Director')
                if relationship.find('isOfficer') and relationship.find('isOfficer').text == '1':
                    officer_title = relationship.find('officerTitle')
                    if officer_title:
                        titles.append(officer_title.text)
                if relationship.find('isTenPercentOwner') and relationship.find('isTenPercentOwner').text == '1':
                    titles.append('10% Owner')
                details['owner_title'] = ', '.join(titles) if titles else 'N/A'
        
        # Transaction information
        transactions = []
        for non_derivative in xml_soup.find_all('nonDerivativeTransaction'):
            trans = {}
            
            # Transaction code (P=Purchase, S=Sale, etc.)
            trans_coding = non_derivative.find('transactionCoding')
            if trans_coding:
                code = trans_coding.find('transactionCode')
                if code:
                    trans_code = code.text
                    trans['type'] = 'Purchase' if trans_code == 'P' else 'Sale' if trans_code == 'S' else trans_code
            
            # Shares
            amounts = non_derivative.find('transactionAmounts')
            if amounts:
                shares = amounts.find('transactionShares')
                if shares:
                    trans['shares'] = shares.find('value').text if shares.find('value') else '0'
                
                price = amounts.find('transactionPricePerShare')
                if price:
                    trans['price'] = price.find('value').text if price.find('value') else '0'
            
            # Calculate dollar amount
            try:
                shares_num = float(trans.get('shares', 0))
                price_num = float(trans.get('price', 0))
                trans['amount'] = shares_num * price_num
            except:
                trans['amount'] = 0
            
            # Security title
            security = non_derivative.find('securityTitle')
            if security:
                trans['security'] = security.find('value').text if security.find('value') else 'Common Stock'
            
            transactions.append(trans)
        
        details['transactions'] = transactions
        
        return details
        
    except Exception as e:
        print(f"Error fetching filing details: {e}")
        return None

def fetch_form4_filings():
    """Fetch latest Form 4 filings from SEC EDGAR"""
    headers = {
        'User-Agent': 'Discord Bot sec-form4-tracker/1.0'
    }
    
    try:
        response = requests.get(SEC_RSS_URL, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'xml')
        entries = soup.find_all('entry')
        
        filings = []
        for entry in entries[:10]:  # Get top 10 latest
            title = entry.find('title').text if entry.find('title') else 'N/A'
            link = entry.find('link')['href'] if entry.find('link') else ''
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
    
    if not details:
        # Fallback if we can't get details
        company = filing['title'].split(' - ')[0] if ' - ' in filing['title'] else 'Unknown Company'
        embed = {
            "title": f"🔔 New Form 4 Filing: {company}",
            "url": filing['link'],
            "color": 3447003,
            "timestamp": datetime.utcnow().isoformat()
        }
    else:
        # Rich notification with details
        issuer_name = details.get('issuer_name', 'N/A')
        ticker = details.get('ticker', 'N/A')
        owner_name = details.get('owner_name', 'N/A')
        owner_title = details.get('owner_title', 'N/A')
        
        # Build transaction summary
        fields = [
            {
                "name": "🏢 Issuer",
                "value": f"**{issuer_name}** ({ticker})",
                "inline": False
            },
            {
                "name": "👤 Reporting Owner",
                "value": owner_name,
                "inline": True
            },
            {
                "name": "💼 Title",
                "value": owner_title,
                "inline": True
            }
        ]
        
        # Add transaction details
        transactions = details.get('transactions', [])
        if transactions:
            for i, trans in enumerate(transactions[:3], 1):  # Limit to 3 transactions
                trans_type = trans.get('type', 'N/A')
                shares = trans.get('shares', '0')
                price = trans.get('price', '0')
                amount = trans.get('amount', 0)
                security = trans.get('security', 'Common Stock')
                
                # Format numbers
                try:
                    shares_fmt = f"{float(shares):,.0f}"
                    price_fmt = f"${float(price):,.2f}"
                    amount_fmt = f"${float(amount):,.2f}"
                except:
                    shares_fmt = shares
                    price_fmt = price
                    amount_fmt = "$0.00"
                
                # Emoji based on transaction type
                emoji = "🟢" if trans_type == "Purchase" else "🔴" if trans_type == "Sale" else "🔵"
                
                trans_value = f"{emoji} **{trans_type}**\n"
                trans_value += f"**{shares_fmt}** shares @ {price_fmt}\n"
                trans_value += f"Total: **{amount_fmt}**\n"
                trans_value += f"Security: {security}"
                
                fields.append({
                    "name": f"Transaction {i}" if len(transactions) > 1 else "Transaction",
                    "value": trans_value,
                    "inline": False
                })
        else:
            fields.append({
                "name": "⚠️ Transaction Details",
                "value": "No transaction data available",
                "inline": False
            })
        
        color = 5763719 if any(t.get('type') == 'Purchase' for t in transactions) else 15158332  # Green for purchase, red for sale
        
        embed = {
            "title": f"📊 Form 4 Filing: {ticker}",
            "url": filing['link'],
            "color": color,
            "fields": fields,
            "footer": {
                "text": "SEC EDGAR Form 4 Tracker"
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload)
        response.raise_for_status()
        print(f"Notification sent for: {details.get('ticker', 'Unknown') if details else 'Unknown'}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}")

def main():
    print(f"Checking for new Form 4 filings at {datetime.now()}")
    
    # Load last seen filings
    last_filings = load_last_filings()
    last_links = set(f['link'] for f in last_filings)
    
    # Fetch current filings
    current_filings = fetch_form4_filings()
    
    if not current_filings:
        print("No filings fetched. Exiting.")
        return
    
    # Find new filings
    new_filings = [f for f in current_filings if f['link'] not in last_links]
    
    if new_filings:
        print(f"Found {len(new_filings)} new filings")
        for filing in reversed(new_filings):  # Send oldest first
            details = get_filing_details(filing['link'])
            send_discord_notification(filing, details)
            time.sleep(1)  # Rate limit between notifications
    else:
        print("No new filings found")
    
    # Save current state
    save_last_filings(current_filings)
    print("State saved successfully")

if __name__ == "__main__":
    main()