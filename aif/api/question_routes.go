package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type RequirementQuestion struct {
	ID         string     `json:"id"`
	RequestID  string     `json:"request_id"`
	Question   string     `json:"question"`
	Reason     *string    `json:"reason"`
	Priority   string     `json:"priority"`
	Status     string     `json:"status"`
	Answer     *string    `json:"answer"`
	AnsweredBy *string    `json:"answered_by"`
	CreatedAt  time.Time  `json:"created_at"`
	AnsweredAt *time.Time `json:"answered_at"`
}

type AnswerInput struct {
	Answer     string `json:"answer"`
	AnsweredBy string `json:"answered_by"`
}

func registerQuestionRoutes(mux *http.ServeMux, pool *pgxpool.Pool) {
	mux.HandleFunc("GET /api/v1/requests/{id}/questions",
		func(w http.ResponseWriter, r *http.Request) {
			requestID := r.PathValue("id")
			if !uuidPattern.MatchString(requestID) {
				http.Error(w, "invalid request id", http.StatusBadRequest)
				return
			}

			ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
			defer cancel()

			var exists bool
			err := pool.QueryRow(ctx,
				`SELECT EXISTS(
                    SELECT 1 FROM aif.requests WHERE id=$1::uuid
                )`, requestID).Scan(&exists)
			if err != nil {
				log.Printf("question request check: %v", err)
				http.Error(w, "database error", 500)
				return
			}
			if !exists {
				http.Error(w, "request not found", 404)
				return
			}

			rows, err := pool.Query(ctx, `
                SELECT id::text, request_id::text, question,
                       reason, priority, status, answer,
                       answered_by, created_at, answered_at
                FROM aif.requirement_questions
                WHERE request_id=$1::uuid
                ORDER BY created_at, id
            `, requestID)
			if err != nil {
				log.Printf("list questions: %v", err)
				http.Error(w, "database error", 500)
				return
			}
			defer rows.Close()

			questions := make([]RequirementQuestion, 0)
			for rows.Next() {
				var q RequirementQuestion
				if err := rows.Scan(
					&q.ID, &q.RequestID, &q.Question,
					&q.Reason, &q.Priority, &q.Status,
					&q.Answer, &q.AnsweredBy,
					&q.CreatedAt, &q.AnsweredAt,
				); err != nil {
					log.Printf("scan question: %v", err)
					http.Error(w, "database error", 500)
					return
				}
				questions = append(questions, q)
			}
			if err := rows.Err(); err != nil {
				log.Printf("iterate questions: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			writeJSON(w, 200, map[string]any{
				"request_id": requestID,
				"count":      len(questions),
				"questions":  questions,
			})
		})

	mux.HandleFunc(
		"POST /api/v1/requests/{id}/questions/{question_id}/answer",
		func(w http.ResponseWriter, r *http.Request) {
			requestID := r.PathValue("id")
			questionID := r.PathValue("question_id")

			if !uuidPattern.MatchString(requestID) ||
				!uuidPattern.MatchString(questionID) {
				http.Error(w, "invalid id", 400)
				return
			}

			r.Body = http.MaxBytesReader(w, r.Body, 16*1024)
			defer r.Body.Close()

			decoder := json.NewDecoder(r.Body)
			decoder.DisallowUnknownFields()

			var input AnswerInput
			if err := decoder.Decode(&input); err != nil {
				http.Error(w, "invalid JSON", 400)
				return
			}
			var extra any
			if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
				http.Error(w, "invalid JSON", 400)
				return
			}

			input.Answer = strings.TrimSpace(input.Answer)
			input.AnsweredBy = strings.TrimSpace(input.AnsweredBy)

			if input.Answer == "" || len(input.Answer) > 10000 ||
				input.AnsweredBy == "" || len(input.AnsweredBy) > 100 {
				http.Error(w, "invalid answer or answered_by", 400)
				return
			}

			ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
			defer cancel()

			tx, err := pool.Begin(ctx)
			if err != nil {
				http.Error(w, "database unavailable", 503)
				return
			}
			defer tx.Rollback(ctx)

			// Serializa cambios de preguntas para esta solicitud.
			var requestStatus string
			err = tx.QueryRow(ctx, `
                SELECT status FROM aif.requests
                WHERE id=$1::uuid FOR UPDATE
            `, requestID).Scan(&requestStatus)

			if errors.Is(err, pgx.ErrNoRows) {
				http.Error(w, "request not found", 404)
				return
			}
			if err != nil {
				log.Printf("lock request: %v", err)
				http.Error(w, "database error", 500)
				return
			}
			if requestStatus != "WAITING_INPUT" {
				http.Error(w, "request is not waiting for input", 409)
				return
			}

			var updatedID string
			err = tx.QueryRow(ctx, `
                UPDATE aif.requirement_questions
                SET answer=$1, answered_by=$2,
                    answered_at=NOW(), status='ANSWERED'
                WHERE id=$3::uuid
                  AND request_id=$4::uuid
                  AND status='OPEN'
                RETURNING id::text
            `, input.Answer, input.AnsweredBy,
				questionID, requestID).Scan(&updatedID)

			if errors.Is(err, pgx.ErrNoRows) {
				http.Error(w, "question not found or already answered", 409)
				return
			}
			if err != nil {
				log.Printf("answer question: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			_, err = tx.Exec(ctx, `
                INSERT INTO aif.events
                  (request_id, event_type, source, payload)
                VALUES (
                  $1::uuid, 'QUESTION_ANSWERED', 'AIF_API',
                  jsonb_build_object(
                    'question_id', $2::text,
                    'answered_by', $3::text
                  )
                )
            `, requestID, updatedID, input.AnsweredBy)
			if err != nil {
				log.Printf("answer event: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			var remaining int
			err = tx.QueryRow(ctx, `
                SELECT COUNT(*) FROM aif.requirement_questions
                WHERE request_id=$1::uuid
                  AND priority='BLOCKING'
                  AND status='OPEN'
            `, requestID).Scan(&remaining)
			if err != nil {
				log.Printf("count pending questions: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			if err := tx.Commit(ctx); err != nil {
				log.Printf("commit answer: %v", err)
				http.Error(w, "database error", 500)
				return
			}

			writeJSON(w, 200, map[string]any{
				"question_id":                  updatedID,
				"status":                       "ANSWERED",
				"remaining_blocking_questions": remaining,
				"request_status":               "WAITING_INPUT",
			})
		})
}
