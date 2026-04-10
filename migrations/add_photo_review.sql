-- Adds gallery photo review workflow.
-- Backward-safe: existing rows default to 'approved'.

ALTER TABLE user_photos
  ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'approved';

-- Enforce allowed values. Uses a named constraint so we can drop/recreate cleanly.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'user_photos_status_check'
  ) THEN
    ALTER TABLE user_photos
      ADD CONSTRAINT user_photos_status_check
      CHECK (status IN ('pending','approved','rejected'));
  END IF;
END $$;

ALTER TABLE user_photos
  ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

ALTER TABLE user_photos
  ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

-- Fast lookups for admin "who has pending photos" queries.
CREATE INDEX IF NOT EXISTS idx_user_photos_gallery_pending
  ON user_photos(user_id)
  WHERE kind = 'gallery' AND status = 'pending';
