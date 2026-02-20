# app/auth_dep.py
import os
from uuid import UUID
from typing import Optional
from fastapi import Header, HTTPException

from dotenv import load_dotenv
load_dotenv()

import jwt  # PyJWT
from jwt import PyJWKClient

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")  # <- only needed for HS256 projects

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is required")

JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
JWT_AUD = "authenticated"  # Supabase default audience

# Lazily init JWK client (only used for RS256)
_JWK_CLIENT = None
def _get_jwk_client():
    global _JWK_CLIENT
    if _JWK_CLIENT is None:
        _JWK_CLIENT = PyJWKClient(JWKS_URL)
    return _JWK_CLIENT

async def get_current_user_id(authorization: Optional[str] = Header(None)) -> UUID:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")

    token = authorization.split(" ", 1)[1]

    try:
        # Peek at header to decide algorithm
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg", "")

        if alg == "RS256":
            # Verify with JWKS
            signing_key = _get_jwk_client().get_signing_key_from_jwt(token).key
            payload = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=JWT_AUD,
                options={"verify_exp": True},
            )
        elif alg == "HS256":
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(500, "Server missing SUPABASE_JWT_SECRET for HS256 tokens")
            payload = jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                audience=JWT_AUD,
                options={"verify_exp": True},
            )
        else:
            raise HTTPException(401, f"Unsupported token alg: {alg}")

        return UUID(payload["sub"])
    except HTTPException:
        # pass through HTTPExceptions we raised
        raise
    except Exception as e:
        # surface exact reason to logs; client still gets 401
        raise HTTPException(401, f"Invalid token: {e}")
