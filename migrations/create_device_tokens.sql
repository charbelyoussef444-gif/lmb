-- Run this in Supabase SQL Editor
-- Creates the device_tokens table for FCM push notifications

CREATE TABLE IF NOT EXISTS device_tokens (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    token TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'android',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, token)
);

-- Index for fast lookup by user_id (used when sending pushes)
CREATE INDEX IF NOT EXISTS idx_device_tokens_user_id ON device_tokens(user_id);

-- RLS: users can only manage their own tokens (if RLS is enabled)
ALTER TABLE device_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can manage their own device tokens"
    ON device_tokens
    FOR ALL
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

-- Allow service role full access (backend uses service role)
CREATE POLICY "Service role full access"
    ON device_tokens
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
