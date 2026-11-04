"""All settings in one place. Each can be overridden with an environment variable."""
import logging
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- limits (protect a shared server from huge files) ---
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))          # pages per PDF
OCR_DPI = int(os.getenv("OCR_DPI", "300"))             # higher = better OCR, slower
OCR_LANG = os.getenv("OCR_LANG", "eng")                # tesseract language(s), e.g. "eng+hin"
# full path to tesseract(.exe) if it is not on PATH, e.g. C:\Tools\Tesseract-OCR\tesseract.exe
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")

# --- classifier ---
RULES_FILE = os.getenv("RULES_FILE", os.path.join(DATA_DIR, "category_rules.json"))
TRAINING_FILE = os.getenv("TRAINING_FILE", os.path.join(DATA_DIR, "training_data.csv"))
# where "learn from my corrections" is saved (point this to a persistent volume in production)
CORRECTIONS_FILE = os.getenv("CORRECTIONS_FILE", os.path.join(DATA_DIR, "user_corrections.csv"))
# set to "false" on a public demo so one user's corrections don't change results for everyone
ENABLE_LEARNING = os.getenv("ENABLE_LEARNING", "true").lower() == "true"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
