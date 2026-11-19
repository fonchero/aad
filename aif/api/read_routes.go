package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"regexp"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var uuidPattern = regexp.MustCompile(
	`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`,
)

type RequestDetail struct {
	ID            string          `json:"id"`
	Title         string          `json:"title"`
	Description   string          `json:"description"`
	RequestType   string          `json:"request_type"`
	Status        string          `json:"status"`
	SourceSystem  *string         `json:"source_system"`
	Specification json.RawMessage `json:"specification"`
	CreatedAt     time.Time       `json:"created_at"`
	UpdatedAt     time.Time       `json:"updated_at"`
}

type RequestEvent struct {
	ID          int64           `json:"id"`
	RequestID   string          `json:"request_id"`
	ExecutionID *string         `json:"execution_id"`
	EventType   string          `json:"event_type"`
	Source      string          `json:"source"`
	Payload     json.RawMessage `json:"payload"`
	CreatedAt   time.Time       `json:"created_at"`
}

func registerReadRoutes(mux *http.ServeMux, pool *pgxpool.Pool) {

	mux.HandleFunc("GET /api/v1/requests/{id}", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")

		if !uuidPattern.MatchString(id) {
			http.Error(w, "invalid request id", http.StatusBadRequest)
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()

		var result RequestDetail

		err := pool.QueryRow(ctx, `
            SELECT
                id::text, title, description, request_type,
                status, source_system, specification,
                created_at, updated_at
            FROM aif.requests
            WHERE id = $1::uuid
        `, id).Scan(
			&result.ID,
			&result.Title,
			&result.Description,
			&result.RequestType,
			&result.Status,
			&result.SourceSystem,
			&result.Specification,
			&result.CreatedAt,
			&result.UpdatedAt,
		)

		if errors.Is(err, pgx.ErrNoRows) {
			http.Error(w, "request not found", http.StatusNotFound)
			return
		}

		if err != nil {
			log.Printf("get request failed: %v", err)
			http.Error(w, "database error", http.StatusInternalServerError)
			return
		}

		writeJSON(w, http.StatusOK, result)
	})

	mux.HandleFunc("GET /api/v1/requests/{id}/events", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")

		if !uuidPattern.MatchString(id) {
			http.Error(w, "invalid request id", http.StatusBadRequest)
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()

		var exists bool
		err := pool.QueryRow(ctx,
			`SELECT EXISTS(SELECT 1 FROM aif.requests WHERE id=$1::uuid)`,
			id,
		).Scan(&exists)

		if err != nil {
			log.Printf("check request failed: %v", err)
			http.Error(w, "database error", http.StatusInternalServerError)
			return
		}

		if !exists {
			http.Error(w, "request not found", http.StatusNotFound)
			return
		}

		rows, err := pool.Query(ctx, `
            SELECT
                id, request_id::text, execution_id::text,
                event_type, source, payload, created_at
            FROM aif.events
            WHERE request_id = $1::uuid
            ORDER BY id ASC
            LIMIT 100
        `, id)

		if err != nil {
			log.Printf("query events failed: %v", err)
			http.Error(w, "database error", http.StatusInternalServerError)
			return
		}
		defer rows.Close()

		events := make([]RequestEvent, 0)

		for rows.Next() {
			var event RequestEvent

			if err := rows.Scan(
				&event.ID,
				&event.RequestID,
				&event.ExecutionID,
				&event.EventType,
				&event.Source,
				&event.Payload,
				&event.CreatedAt,
			); err != nil {
				log.Printf("scan event failed: %v", err)
				http.Error(w, "database error", http.StatusInternalServerError)
				return
			}

			events = append(events, event)
		}

		if err := rows.Err(); err != nil {
			log.Printf("iterate events failed: %v", err)
			http.Error(w, "database error", http.StatusInternalServerError)
			return
		}

		writeJSON(w, http.StatusOK, map[string]any{
			"request_id": id,
			"events":     events,
			"count":      len(events),
		})
	})
}
