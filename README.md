# Healthcare Hiring Lead Generator

This project collects healthcare-tech company leads in the USA, detects whether they are actively hiring for engineering roles, and writes the final hiring-only list to a Google Sheet.

## What This Script Does

- Searches for healthcare-tech company websites (with fallback company seeds if search results are weak)
- Checks company career pages and ATS links (Greenhouse / Lever / Ashby when available)
- Detects hiring signals for these role types:
  - Software Engineer
  - Backend
  - Frontend
  - Full Stack
- Tries to confirm recency (posted in last 30 days using text/date hints)
- Keeps **only hiring companies**
- Writes results to a new Google Sheet

## Output Columns

- Company Name  
- Website  
- Employee Size (approximate)  
- Hiring  
- Open Roles  
- Category  
- CEO (placeholder)  
- CTO (placeholder)  
- Contact Email (domain-based)  
- LinkedIn (generated company URL)


## Project Files

- `healthcare_companies_to_sheets.py` - Main script
- `oauth_client.json` - OAuth desktop client credentials (local only)
- `token.json` - Saved OAuth token after first login (local only)

## Setup

1. Clone the repository and open terminal in project folder.
2. Install dependencies:

```bash
pip install requests beautifulsoup4 pandas google-api-python-client google-auth google-auth-oauthlib
```

3. In Google Cloud:
   - Enable **Google Sheets API**
   - Enable **Google Drive API**
   - Create OAuth client (Desktop app)
4. Download the OAuth JSON and save it as:

`oauth_client.json`

## Run

```bash
python healthcare_companies_to_sheets.py --auth-mode oauth --credentials "oauth_client.json" --count 10 --sheet-title "Hiring Healthcare Tech Companies"
```

First run will show an auth URL:
- Open URL in browser
- Sign in
- Allow permissions

Then script prints:
- Rows inserted
- Google Sheet URL

## Example

```bash
python healthcare_companies_to_sheets.py --auth-mode oauth --credentials "oauth_client.json" --count 20 --sheet-title "USA Healthcare Hiring Leads"
```
python .\healthcare_companies_to_sheets.py --credentials ".\oauth_client.json" --auth-mode oauth --count 20 --sheet-title "USA Healthcare Companies"

## Notes and Limitations

- Hiring detection is heuristic-based (website text + recency hints), so it is useful but not perfect.
- Some company websites block scraping or load jobs via JS, which can reduce matches.
- If strict filtering returns fewer companies, rerun with a higher `--count`.

## Troubleshooting

### Script is slow
- Reduce count (for example, `--count 5`)
- Check internet stability

### OAuth browser error
- Recreate Desktop OAuth client in Google Cloud
- Delete `token.json`
- Run script again

### No hiring companies found
- This can happen due strict filters + source quality
- Re-run later or increase `--count`

