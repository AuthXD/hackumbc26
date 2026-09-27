-- TeachBack Procedure Library: named, saved procedures in Tiger Cloud (PostgreSQL).
-- Idempotent: safe to run repeatedly (npm run tiger:check applies every numbered migration in order).
-- A normal table, not a hypertable: each row is the current definition of a named procedure.
-- Searchable metadata lives in ordinary columns; the validated Procedure itself is JSONB. Keyframe images are
-- not stored here.
CREATE TABLE IF NOT EXISTS teachback_procedures (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    tags TEXT[] NOT NULL DEFAULT '{}',
    detector_kind TEXT NOT NULL,
    step_count INTEGER NOT NULL,
    object_count INTEGER NOT NULL,
    procedure JSONB NOT NULL,
    ai_generated_metadata BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Same slug rule as SavedProcedure.id (derived from the name, at most 40 characters).
    CONSTRAINT teachback_procedures_id_slug CHECK (
        char_length(id) BETWEEN 1 AND 40 AND id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'
    ),
    CONSTRAINT teachback_procedures_name_length CHECK (char_length(name) BETWEEN 1 AND 60),
    CONSTRAINT teachback_procedures_summary_length CHECK (char_length(summary) <= 200),
    CONSTRAINT teachback_procedures_tag_count CHECK (cardinality(tags) <= 3),
    CONSTRAINT teachback_procedures_detector CHECK (detector_kind IN ('color', 'semantic')),
    CONSTRAINT teachback_procedures_step_count CHECK (step_count BETWEEN 1 AND 20),
    CONSTRAINT teachback_procedures_object_count CHECK (object_count BETWEEN 1 AND 12),
    CONSTRAINT teachback_procedures_payload CHECK (jsonb_typeof(procedure) = 'object')
);

-- The library lists newest first.
CREATE INDEX IF NOT EXISTS teachback_procedures_updated ON teachback_procedures (updated_at DESC);

COMMENT ON TABLE teachback_procedures IS
    'TeachBack Procedure Library: named procedures (validated Procedure as JSONB). Every row is re-validated by the app.';
