package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func registerResumeRoutes(mux *http.ServeMux, pool *pgxpool.Pool) {
	mux.HandleFunc(
		"POST /api/v1/requests/{id}/resume",
		func(w http.ResponseWriter, r *http.Request) {
			id := r.PathValue("id")

			if !uuidPattern.MatchString(id) {
				http.Error(w, "invalid request id", 400)
				return
			}

			ctx, cancel := context.WithTimeout(
				r.Context(), 10*time.Second,
			)
			defer cancel()

			tx, err := pool.Begin(ctx)
			if err != nil {
				http.Error(w, "database unavailable", 503)
				return
			}
			defer tx.Rollback(ctx)

			var status string
			err = tx.QueryRow(ctx, `
                SELECT status
                FROM aif.requests
                WHERE id=$1::uuid
                FOR UPDATE
            `, id).Scan(&status)

			if errors.Is(err, pgx.ErrNoRows) {
				http.Error(w, "request not found", 404)
				return
			}
			if err != nil {
				log.Printf("resume request lookup: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			if status != "WAITING_INPUT" {
				http.Error(
					w, "request is not waiting for input", 409,
				)
				return
			}

			var open, answered int
			err = tx.QueryRow(ctx, `
                SELECT
                    COUNT(*) FILTER (
                        WHERE priority='BLOCKING'
                          AND status='OPEN'
                    ),
                    COUNT(*) FILTER (
                        WHERE status='ANSWERED'
                    )
                FROM aif.requirement_questions
                WHERE request_id=$1::uuid
            `, id).Scan(&open, &answered)

			if err != nil {
				log.Printf("resume questions check: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			if open > 0 {
				writeJSON(w, 409, map[string]any{
					"error":     "blocking questions remain",
					"remaining": open,
				})
				return
			}

			if answered == 0 {
				http.Error(
					w, "no answered questions to resume", 409,
				)
				return
			}

			var previousAttempt int
			err = tx.QueryRow(ctx, `
                SELECT COALESCE(MAX(attempt), 0)
                FROM aif.executions
                WHERE request_id=$1::uuid
            `, id).Scan(&previousAttempt)

			if err != nil {
				log.Printf("resume attempt lookup: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			if previousAttempt == 0 {
				http.Error(w, "no previous execution", 409)
				return
			}

			_, err = tx.Exec(ctx, `
                UPDATE aif.requests
                SET status='DRAFT', updated_at=NOW()
                WHERE id=$1::uuid
            `, id)
			if err != nil {
				log.Printf("resume update: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			_, err = tx.Exec(ctx, `
                INSERT INTO aif.events
                    (request_id, event_type, source, payload)
                VALUES (
                    $1::uuid,
                    'RESUME_REQUESTED',
                    'AIF_API',
                    jsonb_build_object(
                        'previous_attempt', $2::integer,
                        'next_attempt', ($2::integer + 1),
                        'answered_questions', $3::integer
                    )
                )
            `, id, previousAttempt, answered)

			if err != nil {
				log.Printf("resume event: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			if err := tx.Commit(ctx); err != nil {
				log.Printf("resume commit: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			writeJSON(w, 202, map[string]any{
				"request_id":   id,
				"status":       "DRAFT",
				"next_attempt": previousAttempt + 1,
				"message":      "Request ready for re-evaluation",
			})
		},
	)
}
