# app/deps/auth.py
from uuid import UUID
from typing import Optional
from fastapi import Header, HTTPException, WebSocket
import jwt
from jwt import PyJWKClient
from app.core.config import settings

SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_JWT_SECRET = settings.SUPABASE_JWT_SECRET  # only if HS256
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is required")

JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
JWT_AUD = "authenticated"
_jwk_client: Optional[PyJWKClient] = None

def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(JWKS_URL)
    return _jwk_client

def verify_token_and_get_user_id(token: str) -> UUID:
    if not token:
        raise HTTPException(401, "Missing bearer token")
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
        if alg == "RS256":
            key = _get_jwk_client().get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token, key, algorithms=["RS256"], audience=JWT_AUD, options={"verify_exp": True}
            )
        elif alg == "HS256":
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(500, "Server missing SUPABASE_JWT_SECRET for HS256 tokens")
            payload = jwt.decode(
                token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience=JWT_AUD, options={"verify_exp": True}
            )
        else:
            raise HTTPException(401, f"Unsupported token alg: {alg}")
        return UUID(payload["sub"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(401, f"Invalid token: {e}")

# Existing HTTP dep (unchanged, but delegate to pure helper)
async def get_current_user_id(authorization: Optional[str] = Header(None)) -> UUID:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1]
    return verify_token_and_get_user_id(token)

# NEW: WebSocket helper (token in ?token=... or Authorization header)
async def get_ws_user_id(ws: WebSocket) -> UUID:
    token = ws.query_params.get("token")
    if not token:
        auth = ws.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1]
    return verify_token_and_get_user_id(token)
