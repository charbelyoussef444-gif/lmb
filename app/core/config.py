# app/core/config.py
from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")  # only for HS256 projects
    STORAGE_BUCKET = os.getenv("STORAGE_BUCKET", "user-photos")

    # CORS
    CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Local dev helper: allow skipping SSL
    LM_INSECURE_SSL = os.getenv("LM_INSECURE_SSL", "0") == "1"

settings = Settings()
