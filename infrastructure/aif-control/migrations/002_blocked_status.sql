BEGIN;

ALTER TABLE aif.requests
    DROP CONSTRAINT requests_status_chk;

ALTER TABLE aif.requests
    ADD CONSTRAINT requests_status_chk
    CHECK (
        status IN (
            'DRAFT',
            'DISCOVERING',
            'WAITING_INPUT',
            'BLOCKED',
            'READY_FOR_BUILD',
            'IN_PROGRESS',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        )
    );

COMMIT;
