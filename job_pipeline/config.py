"""
Central config. Loads from .env — copy .env.example to .env and fill in your values.
Nothing here should ever be committed with real secrets in it.
"""
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")

TARGET_LOCATION = os.getenv("TARGET_LOCATION", "Austin, TX")

MAX_REVIEW_ROUNDS = int(os.getenv("MAX_REVIEW_ROUNDS", "3"))
MIN_KEYWORD_MATCH = float(os.getenv("MIN_KEYWORD_MATCH", "0.70"))
AUTO_IMPROVE = os.getenv("AUTO_IMPROVE", "true").lower() == "true"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
MASTER_RESUME_PATH = os.path.join(DATA_DIR, "master_resume.json")
APPLICATIONS_LOG = os.path.join(STORAGE_DIR, "applications.json")
LEARNINGS_PATH = os.path.join(STORAGE_DIR, "learnings.json")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(STORAGE_DIR, exist_ok=True)
