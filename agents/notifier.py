"""
Agent 4 — Notifier.

Does NOT submit anything. Its only job: once a resume has passed review
and hit the keyword threshold, package everything Gavino needs to submit
it himself — the resume file, the match score, and the fastest apply
path — then tell him it's ready, via email and a local output packet.
"""
import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import config


def best_apply_path(jd_meta: dict) -> str:
    portal = jd_meta.get("portal", "Company careers site")
    url = jd_meta.get("url", "")
    if portal in ("Greenhouse", "Lever", "Ashby", "SmartRecruiters", "Workday", "iCIMS"):
        return f"Apply directly on the {portal} posting (fastest, goes straight to the hiring team): {url}"
    if portal == "LinkedIn":
        return f"Use LinkedIn Easy Apply if available, otherwise check if the company's own careers site has the same posting (often gets more attention than the LinkedIn apply button): {url}"
    if portal == "Indeed":
        return f"Prefer applying via the company's own site if this posting is cross-listed there — recruiters weight direct applications higher than aggregator applies. Indeed link: {url}"
    if url:
        return f"Apply on the company's careers site: {url}"
    return "No URL was provided — paste the job posting link so I can include a direct apply link next time."


def write_local_packet(app_id: str, job_title: str, company: str, resume_path: str,
                        match_result: dict, jd_meta: dict, review_result: dict,
                        audit_result: dict = None) -> str:
    packet = {
        "application_id": app_id,
        "job_title": job_title,
        "company": company,
        "resume_file": resume_path,
        "match_score": match_result.get("match_score"),
        "missing_required_keywords": match_result.get("missing_required", []),
        "missing_preferred_keywords": match_result.get("missing_preferred", []),
        "apply_link": jd_meta.get("url", ""),
        "best_apply_path": best_apply_path(jd_meta),
        "review_passed": review_result.get("pass"),
        "outstanding_review_notes": review_result.get("issues", []),
        "audit": audit_result or {},
        "status": "ready_for_your_review",
    }
    path = os.path.join(config.OUTPUT_DIR, f"{app_id}_notification.json")
    with open(path, "w") as f:
        json.dump(packet, f, indent=2)
    return path


def send_email(job_title: str, company: str, resume_path: str, match_result: dict,
                jd_meta: dict, review_result: dict, audit_result: dict = None):
    if not (config.SMTP_USER and config.SMTP_PASS and config.NOTIFY_EMAIL):
        print("[notifier] SMTP not configured — skipping email, local packet still written.")
        return False

    msg = MIMEMultipart()
    msg["From"] = config.SMTP_USER
    msg["To"] = config.NOTIFY_EMAIL
    msg["Subject"] = f"Resume ready for review: {job_title} @ {company}"

    score = match_result.get("match_score")
    score_pct = f"{score*100:.0f}%" if isinstance(score, (int, float)) else "n/a"
    missing_req = match_result.get("missing_required", [])
    notes = review_result.get("issues", [])

    audit = audit_result or {}
    red_flags = audit.get("red_flags", [])
    flag_lines = "\n".join(f"  - {f.get('flag')} -> fix: {f.get('fix')}" for f in red_flags) or "  none flagged"

    body = f"""Resume is ready for this job - YOU still need to hit submit.

JOB: {job_title}
COMPANY: {company}

RECRUITER AUDIT SCORE: {audit.get("match_score", "n/a")}/100
Verdict: {audit.get("verdict", "n/a")}
ATS keyword coverage: {score_pct}

Missing keywords the ATS will scan for:
  {", ".join(audit.get("missing_keywords", missing_req)) or "none"}

Red flags a hiring manager would spot in 10 seconds:
{flag_lines}

Highest-leverage fix:
  {audit.get("highest_leverage_fix", "n/a")}

BEST WAY TO APPLY:
{best_apply_path(jd_meta)}

Apply link: {jd_meta.get("url", "(no URL - paste it manually)")}

Outstanding review notes:
{chr(10).join(notes) if notes else "None - all three reviewers passed."}

Tailored resume is attached.
"""
    msg.attach(MIMEText(body, "plain"))

    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(resume_path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(resume_path)}"'
        msg.attach(part)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.send_message(msg)
    return True


def notify(app_id: str, job_title: str, company: str, resume_path: str,
           match_result: dict, jd_meta: dict, review_result: dict, audit_result: dict = None):
    local_path = write_local_packet(app_id, job_title, company, resume_path,
                                     match_result, jd_meta, review_result, audit_result)
    emailed = send_email(job_title, company, resume_path, match_result, jd_meta,
                          review_result, audit_result)
    print(f"[notifier] Resume ready for review: {job_title} @ {company}")
    print(f"[notifier] Resume file: {resume_path}")
    print(f"[notifier] Notification packet: {local_path}")
    print(f"[notifier] Email sent: {emailed}")
    return {"local_packet": local_path, "emailed": emailed}
