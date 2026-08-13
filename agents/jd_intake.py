"""
Job Description Intake — not really an 'agent' in the LLM sense, just the
front door. Accepts either raw pasted JD text, or a URL to fetch and
extract from. Also does a best-effort guess at the application portal
(Greenhouse / Lever / Workday / company site) so the notifier can tell you
the fastest way to apply.
"""
import re
import requests
from bs4 import BeautifulSoup

ATS_PATTERNS = {
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "myworkdayjobs.com": "Workday",
    "icims.com": "iCIMS",
    "ashbyhq.com": "Ashby",
    "smartrecruiters.com": "SmartRecruiters",
    "linkedin.com/jobs": "LinkedIn",
    "indeed.com": "Indeed",
}


def detect_portal(url: str) -> str:
    for pattern, name in ATS_PATTERNS.items():
        if pattern in url:
            return name
    return "Company careers site"


def fetch_jd_from_url(url: str) -> dict:
    """Fetch and lightly clean a job posting page. Falls back gracefully —
    some ATS pages render via JS and won't have full text in raw HTML;
    if extraction looks too short, tell the caller so they can paste manually."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobPipelineBot/1.0)"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()

    title_guess = None
    if soup.title and soup.title.string:
        title_guess = soup.title.string.strip()

    return {
        "url": url,
        "portal": detect_portal(url),
        "title_guess": title_guess,
        "text": text,
        "extraction_looks_thin": len(text) < 400,
    }


def from_pasted_text(text: str, source_url: str = "") -> dict:
    return {
        "url": source_url,
        "portal": detect_portal(source_url) if source_url else "Manual paste — apply link needed",
        "title_guess": None,
        "text": text.strip(),
        "extraction_looks_thin": len(text.strip()) < 200,
    }
