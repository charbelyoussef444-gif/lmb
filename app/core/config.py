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

    # Secret key used to HMAC-hash ID numbers for duplicate detection
    KYC_ID_HMAC_KEY = os.getenv("KYC_ID_HMAC_KEY", "")

    # CORS
    CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Local dev helper: allow skipping SSL
    LM_INSECURE_SSL = os.getenv("LM_INSECURE_SSL", "0") == "1"

    # Global matching launch gate. Until this instant, /matching/generate
    # returns ineligible and /matching/status returns launch_pending=true.
    # Users can still build profiles; only matching is held. ISO-8601 with
    # timezone offset. Asia/Beirut DST is +03:00 in June. Override via env
    # to roll launch forward/back without redeploying code.
    MATCHING_LAUNCH_AT = os.getenv("MATCHING_LAUNCH_AT", "2026-06-01T00:00:00+03:00")

settings = Settings()
