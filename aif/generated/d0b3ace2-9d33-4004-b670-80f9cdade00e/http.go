package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"
	"time"
)

var errCustomerNotFound = errors.New("customer not found")

type loan struct {
	ID         int32  `json:"id"`
	LoanNumber string `json:"loan_number"`
	Amount     string `json:"amount"`
	Status     string `json:"status"`
	StartDate  string `json:"start_date"`
}

type customerLoans struct {
	CustomerID int32  `json:"customer_id"`
	Loans      []loan `json:"loans"`
}

type errorResponse struct {
	Error string `json:"error"`
}

type loanReader interface {
	listLoans(context.Context, int32) ([]loan, error)
}

func newHandler(store loanReader, queryTimeout time.Duration) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, struct {
			Status string `json:"status"`
		}{Status: "ok"})
	})
	mux.HandleFunc("GET /api/v1/customers/{customer_id}/loans", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		customerID, ok := parseCustomerID(r.PathValue("customer_id"))
		if !ok {
			writeJSON(w, http.StatusBadRequest, errorResponse{Error: "invalid_customer_id"})
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), queryTimeout)
		defer cancel()
		loans, err := store.listLoans(ctx, customerID)
		switch {
		case errors.Is(err, errCustomerNotFound):
			writeJSON(w, http.StatusNotFound, errorResponse{Error: "customer_not_found"})
		case err != nil:
			log.Print("falló la consulta de préstamos")
			writeJSON(w, http.StatusInternalServerError, errorResponse{Error: "internal_error"})
		default:
			if loans == nil {
				loans = []loan{}
			}
			writeJSON(w, http.StatusOK, customerLoans{CustomerID: customerID, Loans: loans})
		}
	})
	return mux
}

func parseCustomerID(value string) (int32, bool) {
	// PostgreSQL integer is signed and has 32 bits.
	id, err := strconv.ParseUint(value, 10, 31)
	return int32(id), err == nil && id > 0
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		log.Print("falló el envío de la respuesta HTTP")
	}
}
