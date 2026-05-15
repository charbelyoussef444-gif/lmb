-- ─────────────────────────────────────────────────────────────────────────
-- LikeMinded — Production user-data wipe (Postgres tables only)
--
-- DESTRUCTIVE & IRREVERSIBLE.
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor) as the
-- service role. It truncates every user-generated row in the public schema
-- while preserving:
--   • Schema (tables, columns, indexes)
--   • RLS policies
--   • Reference/seed data: personality_traits, locations, interests,
--     onboarding_mcq_config, onboarding_mcq_dynamic
--
-- After this finishes, run 02_wipe_auth_and_storage.py to clear
-- auth.users + the user-photos storage bucket.
--
-- Pre-flight: take a Postgres backup first
--   (Supabase → Database → Backups → Create backup now).
-- ─────────────────────────────────────────────────────────────────────────

BEGIN;

-- Suppress trigger fan-out (audit, event mirroring) during the cleanup so
-- we don't generate millions of dependent rows while emptying the source.
SET LOCAL session_replication_role = replica;

-- Order is explicit for readability; TRUNCATE ... CASCADE would resolve
-- the dependency graph automatically, but listing each table makes the
-- intent reviewable and survives future schema additions cleanly.
TRUNCATE TABLE
  -- conversation graph dependents
  conversation_photo_access,
  conversation_match_feedback,
  messages,
  conversations,
  -- analytics / event log (references users AND messages)
  events,
  -- assessment chatbot history
  onboarding_mcq_answers,
  onboarding_mcq_runs,
  -- KYC / identity verification
  kyc_rejection_reasons,
  kyc_id_hashes,
  kyc_verification,
  -- personality + preferences
  user_personality_pref,
  user_personality,
  user_interests,
  user_photos,
  -- push notifications
  device_tokens,
  -- root user table — MUST be last (parent of all FKs above)
  users
CASCADE;

SET LOCAL session_replication_role = DEFAULT;

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────
-- VERIFICATION — run as a separate statement after COMMIT:
--
-- SELECT 'users'                       AS tbl, count(*) FROM users
-- UNION ALL SELECT 'conversations',          count(*) FROM conversations
-- UNION ALL SELECT 'messages',               count(*) FROM messages
-- UNION ALL SELECT 'events',                 count(*) FROM events
-- UNION ALL SELECT 'user_photos',            count(*) FROM user_photos
-- UNION ALL SELECT 'user_interests',         count(*) FROM user_interests
-- UNION ALL SELECT 'user_personality',       count(*) FROM user_personality
-- UNION ALL SELECT 'kyc_verification',       count(*) FROM kyc_verification
-- UNION ALL SELECT 'device_tokens',          count(*) FROM device_tokens;
--
-- Every row should report 0.
-- ─────────────────────────────────────────────────────────────────────────
