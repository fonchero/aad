package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type statisticsStoreFunc func(context.Context, int32) (loanStatistics, error)

func (f statisticsStoreFunc) LoanStatistics(ctx context.Context, id int32) (loanStatistics, error) {
	return f(ctx, id)
}

func TestHealth(t *testing.T) {
	handler := newHandler(nil)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/health", nil))
	checkJSON(t, response, http.StatusOK, `{"status":"ok"}`)
}

func TestLoanStatistics(t *testing.T) {
	tests := []struct {
		name       string
		pathID     string
		wantID     int32
		stats      loanStatistics
		storeError error
		wantStatus int
		wantBody   string
	}{
		{
			name:       "customer with loans",
			pathID:     "42",
			wantID:     42,
			stats:      loanStatistics{CustomerID: 42, TotalLoans: 6, ActiveLoans: 3, DefaultedLoans: 2, PaidOffLoans: 1},
			wantStatus: http.StatusOK,
			wantBody:   `{"customer_id":42,"total_loans":6,"active_loans":3,"defaulted_loans":2,"paid_off_loans":1}`,
		},
		{
			name:       "customer without loans",
			pathID:     "2147483647",
			wantID:     2147483647,
			stats:      loanStatistics{CustomerID: 2147483647},
			wantStatus: http.StatusOK,
			wantBody:   `{"customer_id":2147483647,"total_loans":0,"active_loans":0,"defaulted_loans":0,"paid_off_loans":0}`,
		},
		{
			name:       "missing customer",
			pathID:     "0001",
			wantID:     1,
			storeError: fmt.Errorf("lookup failed: %w", errCustomerNotFound),
			wantStatus: http.StatusNotFound,
			wantBody:   `{"error":"customer_not_found"}`,
		},
		{
			name:       "store failure",
			pathID:     "1",
			wantID:     1,
			storeError: errors.New("private database detail"),
			wantStatus: http.StatusInternalServerError,
			wantBody:   `{"error":"internal_error"}`,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			storeCalled := false
			handler := newHandler(statisticsStoreFunc(func(ctx context.Context, id int32) (loanStatistics, error) {
				storeCalled = true
				if id != tt.wantID {
					t.Fatalf("store received ID %d, want %d", id, tt.wantID)
				}
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > queryTimeout {
					t.Fatal("store context lacks the expected deadline")
				}
				return tt.stats, tt.storeError
			}))
			req := httptest.NewRequest(http.MethodGet, "/api/v1/customers/"+tt.pathID+"/loan-statistics", nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, req)
			if !storeCalled {
				t.Fatal("store was not called")
			}
			checkJSON(t, response, tt.wantStatus, tt.wantBody)
		})
	}
}

func TestInvalidCustomerID(t *testing.T) {
	handler := newHandler(nil)
	for _, pathID := range []string{"abc", "0", "-1", "+1", "1.5", "2147483648", "1%20", "1%2F2"} {
		t.Run(pathID, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "/api/v1/customers/"+pathID+"/loan-statistics", nil)
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, req)
			checkJSON(t, response, http.StatusBadRequest, `{"error":"invalid_customer_id"}`)
		})
	}
}

func TestRoutesRejectOtherMethods(t *testing.T) {
	handler := newHandler(nil)
	for _, path := range []string{"/health", "/api/v1/customers/1/loan-statistics"} {
		for _, method := range []string{http.MethodHead, http.MethodPost} {
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest(method, path, nil))
			if response.Code != http.StatusMethodNotAllowed || response.Header().Get("Allow") != http.MethodGet {
				t.Errorf("%s %s returned %d, Allow %q", method, path, response.Code, response.Header().Get("Allow"))
			}
		}
	}
}

func checkJSON(t *testing.T, response *httptest.ResponseRecorder, wantStatus int, wantBody string) {
	t.Helper()
	if response.Code != wantStatus {
		t.Errorf("status = %d, want %d", response.Code, wantStatus)
	}
	if got := response.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	if got := strings.TrimSpace(response.Body.String()); got != wantBody {
		t.Errorf("body = %s, want %s", got, wantBody)
	}
}
