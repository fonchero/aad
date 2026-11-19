package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"time"
)

const queryTimeout = 3 * time.Second

func newHandler(store statisticsStore) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", getOnly(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}))
	mux.HandleFunc("/api/v1/customers/{customer_id}/loan-statistics", getOnly(func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseUint(r.PathValue("customer_id"), 10, 31)
		if err != nil || id == 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid_customer_id"})
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), queryTimeout)
		defer cancel()

		stats, err := store.LoanStatistics(ctx, int32(id))
		switch {
		case errors.Is(err, errCustomerNotFound):
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "customer_not_found"})
		case err != nil:
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal_error"})
		default:
			writeJSON(w, http.StatusOK, stats)
		}
	}))

	return mux
}

func getOnly(handler http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			http.Error(w, http.StatusText(http.StatusMethodNotAllowed), http.StatusMethodNotAllowed)
			return
		}
		handler(w, r)
	}
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
