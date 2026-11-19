BEGIN;

CREATE TABLE IF NOT EXISTS aif.build_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    request_id UUID NOT NULL
        REFERENCES aif.requests(id),

    execution_id UUID NOT NULL UNIQUE
        REFERENCES aif.executions(id),

    artifact_path TEXT NOT NULL,
    image_id TEXT NOT NULL,

    manifest JSONB NOT NULL,
    validation JSONB NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_build_artifacts_request
    ON aif.build_artifacts(request_id, created_at DESC);

COMMIT;
