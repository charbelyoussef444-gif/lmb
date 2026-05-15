#!/usr/bin/env python3
"""
LikeMinded — Production auth.users + storage wipe.

DESTRUCTIVE & IRREVERSIBLE. Run AFTER 01_wipe_data_tables.sql.

What it does:
  1. Lists every object in the user-photos storage bucket (recursive) and
     deletes them in batches.
  2. Lists every row in auth.users via the Supabase Admin API and deletes
     each one. The Admin API cascade-handles auth.identities, auth.sessions,
     auth.refresh_tokens — none of those need manual cleanup.

What it does NOT do:
  • Delete public.* tables — that's 01_wipe_data_tables.sql.
  • Take a backup — you must do that yourself in the Supabase dashboard
    BEFORE running this.

Required env:
  SUPABASE_URL                  e.g. https://ogytgxwappaueonoayqi.supabase.co
  SUPABASE_SERVICE_ROLE_KEY     service_role JWT (NOT the anon key)

Optional env:
  STORAGE_BUCKET                defaults to "user-photos"

Usage:
  pip install "supabase>=2.0"
  export SUPABASE_URL=...
  export SUPABASE_SERVICE_ROLE_KEY=...
  python 02_wipe_auth_and_storage.py            # interactive confirmation
  python 02_wipe_auth_and_storage.py --yes      # skip prompt (CI/automation)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List

try:
    from supabase import create_client, Client
except ImportError:
    sys.exit("Install the SDK first:  pip install 'supabase>=2.0'")


SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET = os.environ.get("STORAGE_BUCKET", "user-photos")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    sys.exit(
        "Missing env: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required."
    )


# ── Storage purge ──────────────────────────────────────────────────────
def _list_bucket_recursive(supabase: Client, prefix: str = "") -> List[str]:
    """Return every object path under `prefix` in BUCKET.

    Supabase Storage's list() doesn't recurse, so we walk the tree
    manually: anything with a non-None `id` is a file, otherwise it's a
    folder we descend into. We page in chunks of 1000 to handle deep
    user directories without hitting the default 100-row cap.
    """
    out: List[str] = []
    offset = 0
    PAGE = 1000

    while True:
        items = supabase.storage.from_(BUCKET).list(
            prefix or "",
            {"limit": PAGE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
        )
        if not items:
            break

        for it in items:
            name = it.get("name")
            if not name:
                continue
            full = f"{prefix}/{name}" if prefix else name
            # Heuristic for "this is a folder, not a file": no id and no
            # uploaded metadata. Supabase doesn't expose a clean is_dir
            # flag so we rely on this contract.
            is_folder = it.get("id") is None and not it.get("metadata")
            if is_folder:
                out.extend(_list_bucket_recursive(supabase, full))
            else:
                out.append(full)

        if len(items) < PAGE:
            break
        offset += PAGE
    return out


def purge_storage(supabase: Client) -> int:
    print(f"[storage] Walking bucket '{BUCKET}'...")
    paths = _list_bucket_recursive(supabase)
    total = len(paths)
    print(f"[storage] Found {total} objects.")
    if total == 0:
        return 0

    BATCH = 500
    deleted = 0
    for i in range(0, total, BATCH):
        batch = paths[i : i + BATCH]
        try:
            supabase.storage.from_(BUCKET).remove(batch)
            deleted += len(batch)
            print(f"[storage] Deleted {deleted}/{total}")
        except Exception as e:
            print(f"[storage] Batch {i}-{i + len(batch)} failed: {e}")
    return deleted


# ── auth.users purge ───────────────────────────────────────────────────
def purge_auth_users(supabase: Client) -> int:
    """Delete every row in auth.users via the admin API.

    We re-list from page 1 every iteration because deleting users shifts
    the listing indices — paginating forward while mutating the underlying
    list silently skips users.
    """
    deleted = 0
    failures = 0
    PER_PAGE = 100

    while True:
        try:
            resp = supabase.auth.admin.list_users(page=1, per_page=PER_PAGE)
        except Exception as e:
            print(f"[auth] list_users failed: {e}")
            break

        users = getattr(resp, "users", None) or resp
        if not users:
            break

        for u in users:
            uid = getattr(u, "id", None) or (
                u.get("id") if isinstance(u, dict) else None
            )
            if not uid:
                continue
            try:
                supabase.auth.admin.delete_user(uid)
                deleted += 1
                if deleted % 50 == 0:
                    print(f"[auth] Deleted {deleted}...")
            except Exception as e:
                failures += 1
                print(f"[auth] FAILED to delete {uid}: {e}")
                if failures > 10:
                    print("[auth] Too many failures, aborting.")
                    return deleted
        # Be polite to the rate limiter.
        time.sleep(0.05)

    return deleted


# ── Entry ──────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help='Skip the interactive "WIPE PRODUCTION" prompt.',
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"  Project URL : {SUPABASE_URL}")
    print(f"  Bucket      : {BUCKET}")
    print(f"  Action      : DELETE ALL USERS + ALL STORAGE OBJECTS")
    print("=" * 60)

    if not args.yes:
        ans = input('\nType "WIPE PRODUCTION" to confirm: ').strip()
        if ans != "WIPE PRODUCTION":
            print("Aborted.")
            return 1

    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    print("\n[1/2] Purging storage...")
    n_storage = purge_storage(supabase)
    print(f"[1/2] Done — deleted {n_storage} storage objects.\n")

    print("[2/2] Deleting auth.users...")
    n_users = purge_auth_users(supabase)
    print(f"[2/2] Done — deleted {n_users} auth users.\n")

    print("Reminder: also run 01_wipe_data_tables.sql in the SQL Editor")
    print("if you haven't already. Order doesn't matter for correctness")
    print("(both sides are independent) but you want both to complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
