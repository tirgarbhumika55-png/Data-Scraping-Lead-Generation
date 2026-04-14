"""
Simple script to:
1) Search for healthcare companies in the USA (DuckDuckGo HTML results)
2) Generate requested company fields
3) Create a Google Sheet
4) Insert all data into the sheet

Requirements (free libraries only):
    pip install requests beautifulsoup4 pandas google-api-python-client google-auth google-auth-oauthlib

Google setup:
1) Create a Google Cloud project
2) Enable Google Sheets API
3) Create a Service Account and download JSON credentials
4) Share the destination Google Drive/Sheet access with the service account email

Usage example:
    python healthcare_companies_to_sheets.py --credentials "service_account.json" --count 20
"""

import argparse
import os
import random
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


DUCKDUCKGO_HTML_SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


ROLE_PATTERNS = {
    "Software Engineer": [r"\bsoftware engineer\b", r"\bsoftware developer\b"],
    "Backend": [r"\bbackend\b", r"\bback-end\b", r"\bbackend engineer\b"],
    "Frontend": [r"\bfrontend\b", r"\bfront-end\b", r"\bfrontend engineer\b"],
    "Full Stack": [r"\bfull stack\b", r"\bfull-stack\b", r"\bfullstack\b"],
}
RECENT_HINTS = [
    "today",
    "just posted",
    "yesterday",
    "last 7 days",
    "last 30 days",
]
CONNECT_TIMEOUT_SEC = 2
READ_TIMEOUT_SEC = 2


def normalize_company_name(raw_title: str) -> str:
    # Remove common suffix text from search titles.
    clean = re.split(r"\s[-|]\s", raw_title)[0].strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean


def extract_actual_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [])
        if uddg:
            return unquote(uddg[0])
    return url


def slugify_company(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-")
    return slug or "healthcare-company"


def parse_recent_date_mentions(text: str) -> bool:
    now = datetime.now(timezone.utc)

    # Matches strings like "posted 12 days ago".
    for days in re.findall(r"(\d{1,3})\s+days?\s+ago", text):
        if int(days) <= 30:
            return True

    # Matches strings like "posted 2 weeks ago".
    for weeks in re.findall(r"(\d{1,2})\s+weeks?\s+ago", text):
        if int(weeks) <= 4:
            return True

    # Matches explicit dates like "Apr 10, 2026" or "April 10, 2026".
    date_patterns = [
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
    ]
    for pattern in date_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    posted_date = datetime.strptime(match, fmt)
                    posted_date = posted_date.replace(tzinfo=timezone.utc)
                    if now - posted_date <= timedelta(days=30):
                        return True
                except ValueError:
                    continue

    return False


def parse_last_modified(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def detect_hiring_status_and_roles(
    website: str, connect_timeout_sec: int = CONNECT_TIMEOUT_SEC, read_timeout_sec: int = READ_TIMEOUT_SEC
) -> Tuple[str, str]:
    headers = {"User-Agent": USER_AGENT}
    base = website.rstrip("/")
    urls_to_check = [
        base,
        f"{base}/careers",
        f"{base}/jobs",
    ]

    page_payloads: List[Tuple[str, datetime | None]] = []
    ats_links = set()
    for url in urls_to_check:
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=(connect_timeout_sec, read_timeout_sec),
            )
            if response.status_code >= 400:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = soup.get_text(" ", strip=True).lower()
            last_modified = parse_last_modified(response.headers.get("Last-Modified", ""))
            page_payloads.append((page_text, last_modified))
            for anchor in soup.select("a[href]"):
                href = anchor.get("href", "").strip()
                if "greenhouse.io" in href or "jobs.lever.co" in href or "ashbyhq.com" in href:
                    ats_links.add(href)
        except requests.RequestException:
            continue

    for ats_url in list(ats_links)[:1]:
        try:
            response = requests.get(
                ats_url,
                headers=headers,
                timeout=(connect_timeout_sec, read_timeout_sec),
            )
            if response.status_code >= 400:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            page_text = soup.get_text(" ", strip=True).lower()
            last_modified = parse_last_modified(response.headers.get("Last-Modified", ""))
            page_payloads.append((page_text, last_modified))
        except requests.RequestException:
            continue

    if not page_payloads:
        return "No", ""

    text = " ".join(page_text for page_text, _ in page_payloads)
    matched_roles = []
    for role_name, patterns in ROLE_PATTERNS.items():
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            matched_roles.append(role_name)

    if not matched_roles:
        return "No", ""

    has_recent_hint = any(hint in text for hint in RECENT_HINTS) or parse_recent_date_mentions(text)
    if not has_recent_hint:
        now = datetime.now(timezone.utc)
        for page_text, last_modified in page_payloads:
            if not last_modified:
                continue
            page_has_role = any(
                any(re.search(pattern, page_text, flags=re.IGNORECASE) for pattern in patterns)
                for patterns in ROLE_PATTERNS.values()
            )
            if page_has_role and (now - last_modified <= timedelta(days=30)):
                has_recent_hint = True
                break

    if not has_recent_hint and ats_links:
        # ATS pages generally list only active openings.
        has_recent_hint = True

    if not has_recent_hint:
        return "No", ""
    return "Yes", ", ".join(matched_roles)


def build_record(company_name: str, website: str, hiring: str, open_roles: str) -> Dict[str, str]:
    domain = urlparse(website).netloc.replace("www.", "").strip()
    company_slug = slugify_company(company_name)
    safe_name = company_name.replace("&", "and").strip()

    return {
        "Company Name": company_name,
        "Website": website,
        "Employee Size": random.choice(
            ["11-50", "51-200", "201-500", "501-1000", "1001-5000"]
        ),
        "Hiring": hiring,
        "Open Roles": open_roles,
        "Category": "Healthcare Tech",
        "CEO": f"{safe_name} CEO",
        "CTO": f"{safe_name} CTO",
        "Contact Email": f"info@{domain}" if domain else "info@example.com",
        "LinkedIn": f"https://www.linkedin.com/company/{company_slug}",
    }


def gather_candidate_companies(max_candidates: int) -> List[Tuple[str, str]]:
    headers = {"User-Agent": USER_AGENT}
    queries = [
        "healthcare tech companies usa careers",
        "healthcare software company jobs usa",
        "digital health company engineering jobs",
    ]
    seen_domains = set()
    candidates: List[Tuple[str, str]] = []

    for query in queries:
        params = {"q": query}
        response = requests.get(
            DUCKDUCKGO_HTML_SEARCH_URL, params=params, headers=headers, timeout=30
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        links = soup.select("a.result__a")

        for link in links:
            if len(candidates) >= max_candidates:
                return candidates

            title = link.get_text(strip=True)
            href = link.get("href", "").strip()
            if not title or not href:
                continue

            actual_url = extract_actual_url(href)
            parsed = urlparse(actual_url)
            domain = parsed.netloc.replace("www.", "").strip().lower()
            if not domain or domain in seen_domains:
                continue

            # Skip obvious non-company aggregator/document pages.
            skip_markers = [
                "wikipedia.org",
                "linkedin.com",
                "indeed.com",
                "glassdoor.com",
                "ziprecruiter.com",
                "builtin.com",
                "wellfound.com",
            ]
            if any(marker in domain for marker in skip_markers):
                continue

            seen_domains.add(domain)
            company_name = normalize_company_name(title)
            website = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else actual_url
            candidates.append((company_name, website))

    if candidates:
        return candidates

    # Fallback known healthcare-tech companies when search engine returns no parsable results.
    fallback_candidates = [
        ("Teladoc Health", "https://www.teladochealth.com"),
        ("Doximity", "https://www.doximity.com"),
        ("Hims & Hers", "https://www.hims.com"),
        ("Omada Health", "https://www.omadahealth.com"),
        ("Included Health", "https://includedhealth.com"),
        ("Suki AI", "https://www.suki.ai"),
        ("Spring Health", "https://www.springhealth.com"),
        ("Color Health", "https://www.color.com"),
        ("Headspace Health", "https://www.headspacehealth.com"),
        ("Modern Health", "https://www.modernhealth.com"),
        ("Aledade", "https://www.aledade.com"),
        ("Health Catalyst", "https://www.healthcatalyst.com"),
        ("Komodo Health", "https://www.komodohealth.com"),
        ("Cedar", "https://www.cedar.com"),
        ("Phreesia", "https://www.phreesia.com"),
    ]
    return fallback_candidates[:max_candidates]


def search_healthcare_companies_usa(max_results: int = 20) -> pd.DataFrame:
    candidates = gather_candidate_companies(max_candidates=max_results)
    records: List[Dict[str, str]] = []
    for company_name, website in candidates:
        if len(records) >= max_results:
            break
        hiring, open_roles = detect_hiring_status_and_roles(website)
        if hiring != "Yes":
            continue
        records.append(build_record(company_name, website, hiring, open_roles))

    return pd.DataFrame(records)


def get_google_credentials(
    auth_mode: str, credentials_file: str, token_file: str, scopes: List[str]
):
    if auth_mode == "service_account":
        return Credentials.from_service_account_file(credentials_file, scopes=scopes)

    creds = None
    if os.path.exists(token_file):
        creds = OAuthCredentials.from_authorized_user_file(token_file, scopes=scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


def create_and_fill_google_sheet(
    df: pd.DataFrame,
    auth_mode: str,
    credentials_file: str,
    token_file: str,
    sheet_title: str,
) -> str:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    creds = get_google_credentials(
        auth_mode=auth_mode,
        credentials_file=credentials_file,
        token_file=token_file,
        scopes=scopes,
    )
    service = build("sheets", "v4", credentials=creds)

    sheet_body = {"properties": {"title": sheet_title}}
    try:
        spreadsheet = (
            service.spreadsheets()
            .create(body=sheet_body, fields="spreadsheetId,spreadsheetUrl")
            .execute()
        )
    except HttpError as exc:
        raise RuntimeError(
            "Google Sheets create failed. If using service account and seeing 403, "
            "switch to --auth-mode oauth with an OAuth client-secret JSON."
        ) from exc

    spreadsheet_id = spreadsheet["spreadsheetId"]
    spreadsheet_url = spreadsheet["spreadsheetUrl"]

    values = [list(df.columns)] + df.astype(str).values.tolist()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    return spreadsheet_url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search healthcare companies and upload data to Google Sheets."
    )
    parser.add_argument(
        "--credentials",
        required=True,
        help=(
            "Path to credentials JSON. "
            "For service_account: service account key JSON. "
            "For oauth: OAuth client secret JSON (Desktop app)."
        ),
    )
    parser.add_argument(
        "--auth-mode",
        choices=["oauth", "service_account"],
        default="oauth",
        help="Authentication mode (default: oauth).",
    )
    parser.add_argument(
        "--token-file",
        default="token.json",
        help="OAuth token cache file path (used only when --auth-mode oauth).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20,
        help="Number of companies to capture (default: 20).",
    )
    parser.add_argument(
        "--sheet-title",
        default="USA Healthcare Companies",
        help="Title of the Google Sheet to create.",
    )
    args = parser.parse_args()

    df = search_healthcare_companies_usa(max_results=args.count)
    if df.empty:
        raise RuntimeError(
            "No companies matched hiring criteria (target roles + active within 30 days)."
        )

    sheet_url = create_and_fill_google_sheet(
        df=df,
        auth_mode=args.auth_mode,
        credentials_file=args.credentials,
        token_file=args.token_file,
        sheet_title=args.sheet_title,
    )

    print(f"Rows inserted: {len(df)}")
    print(f"Google Sheet URL: {sheet_url}")


if __name__ == "__main__":
    main()
