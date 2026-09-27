-- TeachBack Setup Check history: one immutable event per completed check, in a TigerData hypertable.
-- Idempotent: safe to run repeatedly (npm run tiger:check applies every numbered migration in order).
-- Append-only by design: the app only INSERTs (ON CONFLICT DO NOTHING); it never UPDATEs or DELETEs events.
CREATE TABLE IF NOT EXISTS teachback_setup_checks (
    checked_at TIMESTAMPTZ NOT NULL,
    event_id UUID NOT NULL,
    setup_id TEXT NOT NULL,
    setup_name TEXT NOT NULL,
    status TEXT NOT NULL,
    correct JSONB NOT NULL,
    missing JSONB NOT NULL,
    unexpected JSONB NOT NULL,
    misplaced JSONB NOT NULL,
    -- Unique keys on a hypertable must include the partition column.
    CONSTRAINT teachback_setup_checks_pkey PRIMARY KEY (event_id, checked_at),
    CONSTRAINT teachback_setup_checks_status CHECK (status IN ('complete', 'needs_attention')),
    CONSTRAINT teachback_setup_checks_setup_id CHECK (
        char_length(setup_id) BETWEEN 1 AND 40 AND setup_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'
    ),
    CONSTRAINT teachback_setup_checks_setup_name CHECK (char_length(setup_name) BETWEEN 1 AND 60),
    CONSTRAINT teachback_setup_checks_lists CHECK (
        jsonb_typeof(correct) = 'array' AND jsonb_typeof(missing) = 'array'
        AND jsonb_typeof(unexpected) = 'array' AND jsonb_typeof(misplaced) = 'array'
    )
) WITH (
    tsdb.hypertable,
    tsdb.partition_column = 'checked_at'
);

-- Recent checks and readiness summaries are always per setup, newest first.
CREATE INDEX IF NOT EXISTS teachback_setup_checks_setup_time
    ON teachback_setup_checks (setup_id, checked_at DESC);

COMMENT ON TABLE teachback_setup_checks IS
    'TeachBack Setup Check history: append-only events (one per completed check). Every row is re-validated by the app.';
