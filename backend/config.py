import os
from dotenv import load_dotenv

load_dotenv()

UPSTAGE_API_KEY = os.getenv("UPSTAGE_API_KEY", "")
UPSTAGE_BASE_URL = "https://api.upstage.ai/v1"
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
DATA_DIR = os.getenv("DATA_DIR", "./data")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
