from fastapi import APIRouter
from supabase import create_client
import os

router = APIRouter()

supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"],
)

@router.get("/waitlist-count")
def waitlist_count():
    # Get exact count; don't use head=True (not supported in your version)
    res = supabase.table("users").select("id", count="exact").execute()
    return {"count": res.count or 0}
