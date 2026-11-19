package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type CreateRequest struct {
	Title        string `json:"title"`
	Description  string `json:"description"`
	RequestType  string `json:"request_type"`
	SourceSystem string `json:"source_system"`
}

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	pool, err := pgxpool.New(ctx, "")
	if err != nil {
		log.Fatal(err)
	}
	defer pool.Close()

	if err := pool.Ping(ctx); err != nil {
		log.Fatalf("database connection failed: %v", err)
	}

	mux := http.NewServeMux()

	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()

		if err := pool.Ping(ctx); err != nil {
			http.Error(w, "database unavailable", http.StatusServiceUnavailable)
			return
		}

		writeJSON(w, http.StatusOK, map[string]string{
			"status":  "ok",
			"service": "aif-api",
		})
	})

	mux.HandleFunc("POST /api/v1/requests", func(w http.ResponseWriter, r *http.Request) {
		r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
		defer r.Body.Close()

		var input CreateRequest
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()

		if err := decoder.Decode(&input); err != nil {
			http.Error(w, "invalid JSON request", http.StatusBadRequest)
			return
		}

		var extra any
		if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
			if err == nil {
				http.Error(w, "multiple JSON values", http.StatusBadRequest)
				return
			}
			if !errors.Is(err, io.EOF) {
				http.Error(w, "invalid JSON request", http.StatusBadRequest)
				return
			}
		}

		input.Title = strings.TrimSpace(input.Title)
		input.Description = strings.TrimSpace(input.Description)
		input.RequestType = strings.ToUpper(strings.TrimSpace(input.RequestType))
		input.SourceSystem = strings.TrimSpace(input.SourceSystem)

		if input.RequestType == "" {
			input.RequestType = "CREATE"
		}

		if input.Title == "" || len(input.Title) > 200 ||
			input.Description == "" || len(input.Description) > 10000 {
			http.Error(w, "invalid title or description", http.StatusBadRequest)
			return
		}

		if input.RequestType != "CREATE" &&
			input.RequestType != "MODIFY" &&
			input.RequestType != "FIX" {
			http.Error(w, "invalid request_type", http.StatusBadRequest)
			return
		}

		if len(input.SourceSystem) > 100 {
			http.Error(w, "invalid source_system", http.StatusBadRequest)
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()

		tx, err := pool.Begin(ctx)
		if err != nil {
			http.Error(w, "database unavailable", http.StatusServiceUnavailable)
			return
		}
		defer tx.Rollback(ctx)

		var id string
		err = tx.QueryRow(ctx, `
            INSERT INTO aif.requests
                (title, description, request_type, source_system)
            VALUES ($1, $2, $3, NULLIF($4, ''))
            RETURNING id::text
        `, input.Title, input.Description,
			input.RequestType, input.SourceSystem).Scan(&id)

		if err != nil {
			log.Printf("insert request failed: %v", err)
			http.Error(w, "request creation failed", http.StatusInternalServerError)
			return
		}

		_, err = tx.Exec(ctx, `
            INSERT INTO aif.events
                (request_id, event_type, source, payload)
            VALUES ($1::uuid, 'REQUEST_CREATED', 'AIF_API',
                    jsonb_build_object('status', 'DRAFT'))
        `, id)

		if err != nil {
			log.Printf("insert event failed: %v", err)
			http.Error(w, "request creation failed", http.StatusInternalServerError)
			return
		}

		if err := tx.Commit(ctx); err != nil {
			log.Printf("commit failed: %v", err)
			http.Error(w, "request creation failed", http.StatusInternalServerError)
			return
		}

		writeJSON(w, http.StatusCreated, map[string]string{
			"id":      id,
			"status":  "DRAFT",
			"message": "Functional request registered",
		})
	})

	registerReadRoutes(mux, pool)
	registerQuestionRoutes(mux, pool)

	server := &http.Server{
		Addr:              ":8080",
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      20 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	log.Println("AIF API listening on port 8080")
	log.Fatal(fmt.Errorf("HTTP server stopped: %w", server.ListenAndServe()))
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(value)
}
