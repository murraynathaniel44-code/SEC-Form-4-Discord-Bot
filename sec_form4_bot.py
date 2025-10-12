import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import os

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
            summary = entry.find('summary').text if entry.find('summary') else ''
            
            # Extract filing info from summary
            filing_info = {
                'title': title,
                'link': link,
                'updated': updated,
                'summary': summary.strip()
            }
            filings.append(filing_info)
        
        return filings
    except Exception as e:
        print(f"Error fetching filings: {e}")
        return []

def send_discord_notification(filing):
    """Send a Discord notification for a new filing"""
    # Parse the title to extract company name
    title_parts = filing['title'].split(' - ')
    company = title_parts[0] if title_parts else 'Unknown Company'
    
    # Create embed
    embed = {
        "title": f"🔔 New Form 4 Filing: {company}",
        "description": filing['summary'][:500] + ('...' if len(filing['summary']) > 500 else ''),
        "url": filing['link'],
        "color": 3447003,  # Blue color
        "fields": [
            {
                "name": "Filing Time",
                "value": filing['updated'],
                "inline": True
            }
        ],
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
        print(f"Notification sent for: {company}")
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
            send_discord_notification(filing)
    else:
        print("No new filings found")
    
    # Save current state
    save_last_filings(current_filings)
    print("State saved successfully")

if __name__ == "__main__":
    main()