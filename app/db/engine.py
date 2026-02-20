# app/db/engine.py
import os
import ssl, certifi
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from app.core.config import settings

def _to_asyncpg_url(url: str) -> str:
    # normalize to asyncpg scheme
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    # asyncpg ignores sslmode in URL; strip if present
    parts = urlsplit(url)
    if parts.query:
        q = [(k, v) for (k, v) in parse_qsl(parts.query, keep_blank_values=True) if k.lower() != "sslmode"]
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
    return url

if not settings.DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL")

DATABASE_URL = _to_asyncpg_url(settings.DATABASE_URL)

# --- TLS context setup -------------------------------------------------------
# 1) Normal secure mode: certifi CA bundle
# 2) Optional extra root CA from .env (EXTRA_CA_CERT)
# 3) Optional *debug only* insecure toggle via .env (LM_INSECURE_SSL=1)

LM_INSECURE_SSL = os.getenv("LM_INSECURE_SSL", "0") == "1"
EXTRA_CA_CERT   = os.getenv("EXTRA_CA_CERT")  # path to PEM/CRT with your interceptor/corp root

if LM_INSECURE_SSL:
    # ⚠️ DEBUG ONLY: disables verification to prove it's a trust problem
    ssl_ctx = ssl._create_unverified_context()
else:
    # Secure context: trust certifi, and optionally also your extra root
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    if EXTRA_CA_CERT and os.path.exists(EXTRA_CA_CERT):
        # Load your corporate/antivirus root CA as an additional trust anchor
        ssl_ctx.load_verify_locations(cafile=EXTRA_CA_CERT)

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"ssl": ssl_ctx},
)
