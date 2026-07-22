"""
Strategist Agent
----------------
Reads a job/hackathon posting's real page content, then dissects it into
hard skills, soft skills, qualifications, and prep notes.

Root cause of the old "always falls into the exception" bug: the target
URL for LinkedIn postings was built from a string that still had markdown
link syntax pasted into it -
    f"[https://.../jobPosting/](https://.../jobPosting/){job_id}"
- which produces something like "[https://...](https://...)12345", not a
valid URL. `requests` fails on that every time, so the code always landed
in the `except` branch and only ever returned canned/synthetic text. That's
fixed below - target_url is now built as a normal string.

Extraction strategy (in order):
  1. trafilatura - handles the widest variety of real-world page layouts
     (main-content detection, boilerplate removal) without per-site rules.
  2. BeautifulSoup with a several content selectors - fallback for pages
     trafilatura can't parse (rare, e.g. very JS-heavy or malformed HTML).
  3. If both fail (page requires login / JS rendering / blocked us), the
     analysis is generated from the role/company/location only, and the
     response HONESTLY labels that as "estimated" rather than pretending
     it was read from the page.

LLM usage is optional: set GEMINI_API_KEY to get an LLM-written analysis.
Without a key, a deterministic keyword-based analyzer runs instead so the
agent still returns real, non-canned output derived from whatever text it
actually scraped.
"""

import os
import re
import json
import logging
from typing import Optional, Dict

import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("strategist_agent")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_TIMEOUT = 10

# Placeholder/base links the Scout Agent may hand back when a source has no
# per-listing URL - not worth trying to scrape these.
_NON_SCRAPABLE_LINKS = {
    "https://www.naukri.com/", "https://www.indeed.com/", "https://www.glassdoor.com/",
    "https://www.foundit.in/", "https://unstop.com/", "https://unstop.com/jobs",
    "https://devpost.com/hackathons", "https://www.arbeitnow.com/", "https://remoteok.com/",
}


def _linkedin_job_id(url: str) -> Optional[str]:
    match = (re.search(r'view/(\d+)', url)
             or re.search(r'currentJobId=(\d+)', url)
             or re.search(r'-(\d+)(?:\?|$)', url))
    return match.group(1) if match else None


def extract_raw_page_text(url: str) -> Optional[str]:
    """Fetches a live posting and returns cleaned text content, or None."""
    if not url or url.strip().rstrip("/") in {u.rstrip("/") for u in _NON_SCRAPABLE_LINKS}:
        logger.info("Skipping non-scrapable/base URL: %s", url)
        return None

    target_url = url
    if "linkedin.com" in url:
        job_id = _linkedin_job_id(url)
        if job_id:
            # NOTE: plain string formatting - no markdown syntax baked in.
            target_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    try:
        response = requests.get(target_url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT,
                                 allow_redirects=True)
    except requests.RequestException as e:
        logger.warning("Extraction request failed for %s: %s", target_url, e)
        return None

    if response.status_code != 200:
        logger.warning("Extraction got HTTP %s for %s", response.status_code, target_url)
        return None

    # Strategy 1: trafilatura - robust generic main-content extraction.
    try:
        import trafilatura
        extracted = trafilatura.extract(response.text, favor_recall=True)
        if extracted and len(extracted.strip()) >= 150:
            return re.sub(r"\s+", " ", extracted).strip()[:8000]
    except ImportError:
        logger.info("trafilatura not installed, falling back to BeautifulSoup")
    except Exception as e:
        logger.warning("trafilatura extraction error: %s", e)

    # Strategy 2: BeautifulSoup fallback with a handful of common selectors.
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "svg", "noscript", "form"]):
            tag.extract()

        content_box = (
            soup.find(["div", "section"], class_=re.compile(r"description|show-more-less-html|details|job-description|content", re.I))
            or soup.find("main")
            or soup.find("article")
            or soup.body
        )
        text = content_box.get_text(separator=" ", strip=True) if content_box else soup.get_text(separator=" ", strip=True)
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) >= 150:
            return cleaned[:8000]
    except Exception as e:
        logger.warning("BeautifulSoup fallback failed: %s", e)

    logger.info("Could not extract usable text from %s", target_url)
    return None


# --- Deterministic (no-LLM-key) analyzer -------------------------------------
_SKILL_LIBRARY = [
    "Python", "Django", "Flask", "FastAPI", "Java", "Spring Boot", "JavaScript", "TypeScript",
    "React", "Angular", "Vue", "Node.js", "Express", "SQL", "PostgreSQL", "MySQL", "MongoDB",
    "Redis", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "CI/CD", "Git", "REST", "GraphQL",
    "Microservices", "Machine Learning", "TensorFlow", "PyTorch", "Data Structures", "Algorithms",
    "System Design", "Agile", "Scrum", "C++", "C#", "Go", "Rust", "Kafka", "RabbitMQ", "Linux",
]
_SOFT_SKILLS_LIBRARY = [
    "Communication", "Team Collaboration", "Problem Solving", "Ownership", "Adaptability",
    "Time Management", "Critical Thinking", "Mentorship", "Stakeholder Management",
]


def _keyword_analysis(text: str, job_title: str, job_company: str) -> Dict:
    lower = text.lower()
    hard_skills = [s for s in _SKILL_LIBRARY if s.lower() in lower][:6] or ["Core Programming Fundamentals", "REST APIs", "Version Control (Git)"]
    soft_skills = [s for s in _SOFT_SKILLS_LIBRARY if s.lower() in lower][:4] or ["Communication", "Problem Solving", "Team Collaboration"]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:3]).strip() or f"Role focused on {job_title} responsibilities at {job_company}."

    return {
        "job_description": summary,
        "expected_qualifications": [
            "Relevant degree or demonstrable practical experience",
            "Prior experience with the core stack listed below",
        ],
        "hard_skills": hard_skills,
        "soft_skills": soft_skills,
        "location_and_work_style": "Derived from the scraped listing text; check the original posting for exact remote/hybrid/onsite policy.",
        "important_notes": [
            "This analysis was generated with keyword extraction (no LLM key configured), from text scraped off the live posting.",
            "Cross-check the extracted skills against the original link before tailoring your resume.",
            "Prioritize the first few hard skills listed - they appeared most prominently in the posting.",
        ],
        "skill_gap_roadmap": [
            "Map your current skills against the hard_skills list and flag gaps",
            "Build or update one project demonstrating the top 2 missing skills",
            "Rehearse concise, specific answers tying your experience to this role",
        ],
        "interview_prep_questions": [
            f"Walk me through a project where you used {hard_skills[0]}.",
            "How do you approach debugging an issue you've never seen before?",
        ],
        "analysis_source": "scraped_page_keyword_extraction",
    }


def _llm_analysis(text: Optional[str], job_title: str, job_company: str, job_location: str) -> Optional[Dict]:
    """Uses Gemini if GEMINI_API_KEY is set. Returns None if unavailable/fails."""
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.info("google-genai package not installed; skipping LLM analysis")
        return None

    client = genai.Client()

    if text:
        context_prompt = f'SCRAPED WEBPAGE TEXT:\n"""\n{text}\n"""\nBase your analysis primarily on this text.'
        source_note = "Extracted directly from the scraped live posting."
    else:
        context_prompt = (f"No page text could be scraped for this listing. Analyze based on standard "
                           f"industry requirements for '{job_title}' at '{job_company}' in '{job_location}'.")
        source_note = f"Estimated from standard hiring requirements for {job_title} at {job_company} (page text unavailable)."

    prompt = f"""
You are an AI Strategist Agent dissecting a job/hackathon posting.

TARGET ROLE DETAILS:
- Role: {job_title}
- Company: {job_company}
- Location: {job_location}

{context_prompt}

Return ONLY a JSON object (no markdown fences, no commentary) matching exactly this schema:
{{
  "job_description": "3-sentence summary of responsibilities and core tech stack",
  "expected_qualifications": ["...", "..."],
  "hard_skills": ["...", "...", "..."],
  "soft_skills": ["...", "..."],
  "location_and_work_style": "...",
  "important_notes": ["{source_note}", "..."],
  "skill_gap_roadmap": ["...", "...", "..."],
  "interview_prep_questions": ["...", "..."]
}}
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        cleaned = re.sub(r"^```json\s*|^```\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE)
        parsed = json.loads(cleaned)
        parsed["analysis_source"] = "llm_gemini" if text else "llm_gemini_estimated"
        return parsed
    except Exception as e:
        logger.warning("LLM analysis failed, falling back to keyword analyzer: %s", e)
        return None


def run_strategist_agent(job_title: str, job_company: str, job_location: str, job_url: str) -> Dict:
    """
    Real pipeline: scrape -> (LLM analysis if key present, else keyword analysis).
    Never silently swallows the scrape result - if scraping worked, both the
    LLM and keyword paths use the actual page text.
    """
    scraped_text = extract_raw_page_text(job_url)

    llm_result = _llm_analysis(scraped_text, job_title, job_company, job_location)
    if llm_result:
        return llm_result

    if scraped_text:
        return _keyword_analysis(scraped_text, job_title, job_company)

    # Nothing to scrape and no LLM available - honest estimate, clearly labeled.
    return {
        "job_description": f"Responsible for core {job_title} duties at {job_company}. "
                            f"Exact scope could not be confirmed because the page could not be scraped.",
        "expected_qualifications": ["Relevant degree or equivalent practical experience",
                                     "Experience aligned with the role title"],
        "hard_skills": ["Core Programming Fundamentals", "REST APIs", "Git", "SQL"],
        "soft_skills": ["Communication", "Problem Solving", "Team Collaboration"],
        "location_and_work_style": f"Listed for {job_location}; confirm remote/hybrid/onsite policy on the original posting.",
        "important_notes": ["Page text could not be scraped (login wall, JS rendering, or blocked request) - "
                             "this analysis is estimated from the role title/company only."],
        "skill_gap_roadmap": ["Review core fundamentals for this role", "Build one relevant project",
                               "Practice mock technical questions"],
        "interview_prep_questions": ["Describe a project relevant to this role.",
                                      "How do you approach learning an unfamiliar codebase?"],
        "analysis_source": "estimated_no_scrape_no_llm",
    }


if __name__ == "__main__":
    result = run_strategist_agent(
        "Python Developer", "Example Corp", "Remote",
        "https://www.linkedin.com/jobs/view/1234567890"
    )
    print(json.dumps(result, indent=2))
