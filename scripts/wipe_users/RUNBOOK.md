# Production User Wipe — Runbook

**DESTRUCTIVE. IRREVERSIBLE.** Read this end-to-end before running anything.

Wipes every user from the production Supabase project (`ogytgxwappaueonoayqi`):
- All rows in `public.users` and every table that FKs to it
- All Supabase auth users (`auth.users`, `auth.identities`, `auth.sessions`, `auth.refresh_tokens`)
- Every object in the `user-photos` storage bucket

Preserves: schema, RLS policies, indexes, reference/seed tables (`personality_traits`, `locations`, `interests`, `onboarding_mcq_config`, `onboarding_mcq_dynamic`).

---

## Pre-flight (mandatory)

1. **Take a Postgres backup.** Supabase Dashboard → Database → Backups → "Create backup now". Wait for completion. This is your only rollback path — there is no other one.
2. **Confirm the project URL.** You should be operating against `https://ogytgxwappaueonoayqi.supabase.co`. If you accidentally point at a different project, this script will happily delete everything there too.
3. **Pause anything that writes users.** If you have a beta tester signup form open, close it. If you have background workers / cron jobs that touch users, stop them. A new signup mid-wipe means orphaned rows.
4. **Notify any internal stakeholders** if applicable.

---

## Step 1 — Wipe data tables (SQL Editor)

1. Open Supabase Dashboard → SQL Editor.
2. Open the file `01_wipe_data_tables.sql` from this directory.
3. Paste the contents into the editor.
4. Click **Run**. Expected runtime: < 5 seconds.
5. Verify with the SELECT at the bottom of the file. Every count should be `0`.

---

## Step 2 — Wipe auth.users and storage (Python script)

```bash
# From the repo root
cd lmb/scripts/wipe_users
pip install "supabase>=2.0"

export SUPABASE_URL="https://ogytgxwappaueonoayqi.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="<paste service_role key from Supabase Settings → API>"
# Optional override:
# export STORAGE_BUCKET="user-photos"

python 02_wipe_auth_and_storage.py
```

You will be prompted to type `WIPE PRODUCTION` (exact spelling, all caps) to confirm. Anything else aborts.

The script first deletes every storage object in the `user-photos` bucket, then iterates `auth.users` deleting each via the Admin API. Progress is printed every 50 deletions.

---

## Step 3 — Sanity check

In the Supabase Dashboard:

- **Authentication → Users** — should show "No users".
- **Storage → user-photos** — should be empty.
- **Database → Table Editor → users** — should be empty.
- Re-run the verification SELECT from Step 1 — all counts still `0`.

---

## What this does NOT do

- It does NOT clear the matching launch gate. Users who sign up between now and June 1, 2026 will still be blocked at `/matching/generate` and `/matching/status` by `MATCHING_LAUNCH_AT` until the launch instant — that's the intended behavior.
- It does NOT delete the reference data (countries, cities, interests catalog, personality traits, MCQ question bank). Those survive so the next user who signs up has a working app.
- It does NOT delete the system bot user (UUID `00000000-0000-0000-0000-000000000001`) if one exists. If you've seeded a system bot row in `public.users`, you'll need to re-insert it after the wipe — the truncation drops it.

---

## Rollback

Restore from the backup taken in Pre-flight, via Supabase Dashboard → Database → Backups → "Restore". Anything written between the backup and the restore is lost.

If you skipped Pre-flight: there is no rollback. The deletion is permanent.
