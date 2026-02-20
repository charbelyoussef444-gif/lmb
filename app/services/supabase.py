# app/services/supabase.py
from supabase import create_client, Client
from app.core.config import settings

if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

sb: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
