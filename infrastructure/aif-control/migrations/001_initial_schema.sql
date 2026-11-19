BEGIN;

CREATE SCHEMA IF NOT EXISTS aif;

-- 1. Solicitudes funcionales
CREATE TABLE aif.requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    request_type VARCHAR(20) NOT NULL DEFAULT 'CREATE',
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
    source_system VARCHAR(100),
    specification JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT requests_type_chk CHECK (
        request_type IN ('CREATE', 'MODIFY', 'FIX')
    ),
    CONSTRAINT requests_status_chk CHECK (
        status IN (
            'DRAFT',
            'DISCOVERING',
            'WAITING_INPUT',
            'READY_FOR_BUILD',
            'IN_PROGRESS',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )
    )
);

-- 2. Preguntas y aclaraciones funcionales
CREATE TABLE aif.requirement_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL
        REFERENCES aif.requests(id),
    question TEXT NOT NULL,
    reason TEXT,
    priority VARCHAR(20) NOT NULL DEFAULT 'BLOCKING',
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    answer TEXT,
    answered_by VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at TIMESTAMPTZ,

    CONSTRAINT questions_priority_chk CHECK (
        priority IN ('BLOCKING', 'OPTIONAL')
    ),
    CONSTRAINT questions_status_chk CHECK (
        status IN ('OPEN', 'ANSWERED', 'CANCELLED')
    ),
    CONSTRAINT questions_answer_chk CHECK (
        status <> 'ANSWERED'
        OR (answer IS NOT NULL AND answered_at IS NOT NULL)
    )
);

-- 3. Ejecuciones de AIF
CREATE TABLE aif.executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL
        REFERENCES aif.requests(id),
    attempt INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(20) NOT NULL DEFAULT 'QUEUED',
    agent_provider VARCHAR(50),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT executions_attempt_chk CHECK (attempt > 0),
    CONSTRAINT executions_status_chk CHECK (
        status IN (
            'QUEUED',
            'RUNNING',
            'WAITING_INPUT',
            'SUCCEEDED',
            'FAILED',
            'CANCELLED'
        )
    ),
    CONSTRAINT executions_request_attempt_uq
        UNIQUE (request_id, attempt)
);

-- 4. Eventos para auditoría y monitoreo
CREATE TABLE aif.events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL
        REFERENCES aif.requests(id),
    execution_id UUID
        REFERENCES aif.executions(id),
    event_type VARCHAR(100) NOT NULL,
    source VARCHAR(100) NOT NULL DEFAULT 'AIF',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índices
CREATE INDEX idx_requests_status
    ON aif.requests(status);

CREATE INDEX idx_questions_request_status
    ON aif.requirement_questions(request_id, status);

CREATE INDEX idx_executions_status
    ON aif.executions(status, created_at);

CREATE INDEX idx_events_request_id
    ON aif.events(request_id, id);

CREATE INDEX idx_events_execution_id
    ON aif.events(execution_id, id)
    WHERE execution_id IS NOT NULL;

COMMIT;
