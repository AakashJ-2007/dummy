"""
Scout Agent
-----------
Continuously scans real, publicly reachable sources for jobs, internships,
and hackathons. Every function here hits a live endpoint - none of them
fabricate data by cycling through hardcoded company name lists.

Sources used (all reachable without an API key):
  - LinkedIn public "guest" job search endpoint
  - Unstop public opportunity search API
  - RemoteOK public JSON job feed
  - Arbeitnow public job-board API
  - Devpost public hackathons API

NOTE ON RELIABILITY:
LinkedIn's guest endpoint and Unstop's API are unofficial - they are not
guaranteed by those companies and can rate-limit or block an IP that sends
too many requests too quickly. RemoteOK, Arbeitnow, and Devpost are stable,
documented-enough public JSON feeds and are the most reliable sources here.
If you need guaranteed, ToS-compliant coverage of Indeed/Glassdoor/Naukri,
those require a paid/partner API (e.g. Adzuna, Jooble) - there is a clearly
marked extension point below for plugging one in.
"""

import re
import time
import logging
from typing import List, Dict, Optional

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scout_agent")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 10


def _safe_get(url: str, **kwargs) -> Optional[requests.Response]:
    """Single place to do HTTP GETs with consistent error handling/logging."""
    headers = {**DEFAULT_HEADERS, **kwargs.pop("headers", {})}
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers, **kwargs)
        if resp.status_code != 200:
            logger.warning("GET %s -> HTTP %s", url, resp.status_code)
            return None
        return resp
    except requests.RequestException as e:
        logger.warning("GET %s failed: %s", url, e)
        return None


# --- 1. LINKEDIN SCOUT ------------------------------------------------------
def fetch_linkedin_jobs(keywords: str = "Python Developer", location: str = "Remote",
                         max_pages: int = 2) -> List[Dict]:
    """
    Uses LinkedIn's public guest job-search endpoint (no login required).
    This is the same endpoint linkedin.com/jobs uses server-side for its
    "load more" pagination, exposed at a stable public path.
    """
    from bs4 import BeautifulSoup  # local import so the module still loads if bs4 is missing

    base_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    jobs = []
    seen_links = set()

    for page in range(max_pages):
        params = {
            "keywords": keywords,
            "location": location,
            "start": page * 25,
        }
        resp = _safe_get(base_url, params=params)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("li")
        if not cards:
            break

        for card in cards:
            title_elem = card.find("h3", class_="base-search-card__title")
            company_elem = card.find("h4", class_="base-search-card__subtitle")
            location_elem = card.find("span", class_="job-search-card__location")
            link_elem = card.find("a", class_="base-card__full-link")

            if title_elem and company_elem and link_elem:
                link = link_elem["href"].split("?")[0].strip()
                if link in seen_links:
                    continue
                seen_links.add(link)
                jobs.append({
                    "source": "LinkedIn",
                    "type": "job",
                    "title": title_elem.get_text(strip=True),
                    "company": company_elem.get_text(strip=True),
                    "location": location_elem.get_text(strip=True) if location_elem else location,
                    "link": link,
                })

        time.sleep(0.5)  # be polite between pages

    logger.info("LinkedIn: fetched %d jobs", len(jobs))
    return jobs


# --- 2. UNSTOP SCOUT ---------------------------------------------------------
def fetch_unstop_opportunities(keywords: str = "", opportunity_type: str = "jobs",
                                limit: int = 20) -> List[Dict]:
    """Uses Unstop's public opportunity-search API (used by their own front end)."""
    api_url = "https://unstop.com/api/public/opportunity/search-new"
    params = {"opportunity": opportunity_type, "per_page": limit, "page": 1}
    if keywords:
        params["searchKeyword"] = keywords

    items = []
    resp = _safe_get(api_url, headers={**DEFAULT_HEADERS, "Accept": "application/json"}, params=params)
    if resp is None:
        return items

    try:
        data = resp.json().get("data", {}).get("data", [])
    except ValueError:
        logger.warning("Unstop: response was not valid JSON")
        return items

    for item in data:
        title = item.get("title")
        if not title:
            continue
        org = (item.get("organisation") or {}).get("name") or item.get("company_name", "Unstop Employer")
        slug = item.get("public_url") or item.get("site_url") or item.get("slug", "")
        link = slug if slug.startswith("http") else f"https://unstop.com/{slug}".rstrip("/")
        items.append({
            "source": "Unstop",
            "type": "hackathon" if opportunity_type != "jobs" else "job",
            "title": title,
            "company": org,
            "location": item.get("region") or "Online / On-site",
            "link": link or "https://unstop.com/",
        })

    logger.info("Unstop: fetched %d items", len(items))
    return items


# --- 3. REMOTEOK SCOUT -------------------------------------------------------
def fetch_remoteok_jobs(keywords: str = "python", limit: int = 20) -> List[Dict]:
    """RemoteOK exposes its entire live job board as public JSON at /api."""
    resp = _safe_get("https://remoteok.com/api")
    if resp is None:
        return []

    try:
        data = resp.json()
    except ValueError:
        logger.warning("RemoteOK: response was not valid JSON")
        return []

    # First element is a legal/metadata blob, not a job - skip it.
    listings = data[1:] if data and isinstance(data, list) else []
    kw = keywords.lower()
    jobs = []
    for item in listings:
        position = item.get("position", "")
        tags = " ".join(item.get("tags", []))
        haystack = f"{position} {tags} {item.get('company', '')}".lower()
        if kw and kw not in haystack and not any(k in haystack for k in kw.split()):
            continue
        jobs.append({
            "source": "RemoteOK",
            "type": "job",
            "title": position or "Remote Role",
            "company": item.get("company", "Unknown"),
            "location": item.get("location") or "Remote",
            "link": item.get("url") or f"https://remoteok.com{item.get('slug', '')}",
        })
        if len(jobs) >= limit:
            break

    logger.info("RemoteOK: fetched %d jobs", len(jobs))
    return jobs


# --- 4. ARBEITNOW SCOUT -------------------------------------------------------
def fetch_arbeitnow_jobs(keywords: str = "", location: str = "", limit: int = 20) -> List[Dict]:
    """Arbeitnow's public job-board API - no key required."""
    resp = _safe_get("https://www.arbeitnow.com/api/job-board-api")
    if resp is None:
        return []

    try:
        data = resp.json().get("data", [])
    except ValueError:
        logger.warning("Arbeitnow: response was not valid JSON")
        return []

    kw = keywords.lower()
    loc = location.lower()
    jobs = []
    for item in data:
        title = item.get("title", "")
        tags = " ".join(item.get("tags", []) + item.get("job_types", []))
        haystack = f"{title} {tags}".lower()
        if kw and kw not in haystack:
            continue
        item_location = item.get("location", "") or ""
        if loc and loc not in ["remote", ""] and loc not in item_location.lower() and not item.get("remote"):
            continue
        jobs.append({
            "source": "Arbeitnow",
            "type": "job",
            "title": title,
            "company": item.get("company_name", "Unknown"),
            "location": "Remote" if item.get("remote") else (item_location or "Not specified"),
            "link": item.get("url", "https://www.arbeitnow.com/"),
        })
        if len(jobs) >= limit:
            break

    logger.info("Arbeitnow: fetched %d jobs", len(jobs))
    return jobs


# --- 5. DEVPOST SCOUT (Hackathons) -------------------------------------------
def fetch_devpost_hackathons(keywords: str = "", limit: int = 20) -> List[Dict]:
    """Devpost's public hackathons API - powers devpost.com/hackathons itself."""
    params = {"status[]": "open"}
    if keywords:
        params["search"] = keywords

    resp = _safe_get("https://devpost.com/api/hackathons", headers={**DEFAULT_HEADERS, "Accept": "application/json"},
                      params=params)
    if resp is None:
        return []

    try:
        hackathons = resp.json().get("hackathons", [])
    except ValueError:
        logger.warning("Devpost: response was not valid JSON")
        return []

    items = []
    for h in hackathons[:limit]:
        items.append({
            "source": "Devpost",
            "type": "hackathon",
            "title": h.get("title", "Untitled Hackathon"),
            "company": (h.get("organization_name") or "Devpost Community"),
            "location": "Online" if h.get("displayed_location", {}).get("location") is None
                        else h["displayed_location"]["location"],
            "link": h.get("url", "https://devpost.com/hackathons"),
            "submission_deadline": h.get("submission_period_dates"),
            "prize_amount": h.get("prize_amount"),
        })

    logger.info("Devpost: fetched %d hackathons", len(items))
    return items


# --- EXTENSION POINT ----------------------------------------------------------
# Naukri, Indeed, and Glassdoor render listings via JS and/or block plain
# `requests` scraping. To add real coverage for them without violating their
# ToS, plug in a partner API here, for example Adzuna (free tier, needs an
# APP_ID/APP_KEY from https://developer.adzuna.com/):
#
# def fetch_adzuna_jobs(keywords, location, app_id, app_key, country="in", limit=20):
#     url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
#     params = {"app_id": app_id, "app_key": app_key, "what": keywords,
#               "where": location, "results_per_page": limit}
#     resp = _safe_get(url, params=params)
#     ...
# ------------------------------------------------------------------------------


def run_scout(keywords: str = "Python Developer", location: str = "Remote",
              sources: Optional[List[str]] = None, limit_per_source: int = 15) -> Dict[str, List[Dict]]:
    """
    Orchestrates all scout sources. `sources` filters which ones run
    (any of: linkedin, unstop, remoteok, arbeitnow, devpost). Defaults to all.
    Returns {"jobs": [...], "hackathons": [...]} - already de-duplicated by link.
    """
    sources = sources or ["linkedin", "unstop", "remoteok", "arbeitnow", "devpost"]
    all_results: List[Dict] = []

    if "linkedin" in sources:
        all_results += fetch_linkedin_jobs(keywords, location, max_pages=2)
    if "unstop" in sources:
        all_results += fetch_unstop_opportunities(keywords, "jobs", limit_per_source)
    if "remoteok" in sources:
        all_results += fetch_remoteok_jobs(keywords, limit_per_source)
    if "arbeitnow" in sources:
        all_results += fetch_arbeitnow_jobs(keywords, location, limit_per_source)
    if "devpost" in sources:
        all_results += fetch_devpost_hackathons(keywords, limit_per_source)

    seen = set()
    deduped = []
    for item in all_results:
        key = item["link"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    jobs = [i for i in deduped if i["type"] == "job"]
    hackathons = [i for i in deduped if i["type"] == "hackathon"]

    logger.info("Scout run complete: %d jobs, %d hackathons", len(jobs), len(hackathons))
    return {"jobs": jobs, "hackathons": hackathons}


if __name__ == "__main__":
    results = run_scout("React Developer", "Remote")
    print(f"Jobs found: {len(results['jobs'])}")
    print(f"Hackathons found: {len(results['hackathons'])}")
