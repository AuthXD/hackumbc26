-- TeachBack Setup Check: saved setups in Tiger Cloud (PostgreSQL).
-- Idempotent: safe to run repeatedly (npm run tiger:check applies it).
-- A normal table, not a hypertable: each row is the current definition of a named setup,
-- not a time-series event.
CREATE TABLE IF NOT EXISTS teachback_setups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    objects JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Same slug rule as SavedSetup.id (derived from the name, at most 40 characters).
    CONSTRAINT teachback_setups_id_slug CHECK (
        char_length(id) BETWEEN 1 AND 40 AND id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'
    ),
    CONSTRAINT teachback_setups_name_length CHECK (char_length(name) BETWEEN 1 AND 60),
    -- A JSON array of 1 to 6 objects. CASE guards jsonb_array_length against non-arrays.
    CONSTRAINT teachback_setups_objects_array CHECK (
        CASE WHEN jsonb_typeof(objects) = 'array'
             THEN jsonb_array_length(objects) BETWEEN 1 AND 6
             ELSE false
        END
    )
    -- No updated_at >= created_at check: created_at is the laptop's capture time and updated_at is
    -- the server clock, so ordinary clock skew would reject valid saves.
);

COMMENT ON TABLE teachback_setups IS
    'TeachBack Setup Check: current named setups (label + zone per object). Every row is re-validated by the app.';
