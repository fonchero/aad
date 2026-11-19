package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"
)

const customerPath = "/api/v1/customers/"

type loanReaderFunc func(context.Context, int32) ([]loan, error)

func (f loanReaderFunc) listLoans(ctx context.Context, id int32) ([]loan, error) {
	return f(ctx, id)
}

func TestInvalidCustomerID(t *testing.T) {
	for _, value := range []string{"0", "-1", "%2B1", "1.0", "1e2", "abc", "%201", "1%20", "1%2F2", "%EF%BC%91", "2147483648", "999999999999999999999"} {
		t.Run(value, func(t *testing.T) {
			store := loanReaderFunc(func(context.Context, int32) ([]loan, error) {
				t.Fatal("invalid ID reached the loan reader")
				return nil, nil
			})
			res := httptest.NewRecorder()
			newHandler(store, time.Second).ServeHTTP(res, httptest.NewRequest(http.MethodGet, customerPath+value+"/loans", nil))
			assertJSON(t, res, http.StatusBadRequest, `{"error":"invalid_customer_id"}`)
		})
	}
}

func TestCustomerLoans(t *testing.T) {
	tests := []struct {
		name       string
		pathID     string
		customerID int32
		loans      []loan
		err        error
		status     int
		body       string
	}{
		{
			name: "unknown customer", pathID: "1", customerID: 1,
			err: errCustomerNotFound, status: http.StatusNotFound,
			body: `{"error":"customer_not_found"}`,
		},
		{
			name: "existing customer with no loans", pathID: "0007", customerID: 7,
			status: http.StatusOK,
			body:   `{"customer_id":7,"loans":[]}`,
		},
		{
			name: "all loan statuses in order", pathID: "2147483647", customerID: 2147483647,
			loans: []loan{
				{ID: 2, LoanNumber: "L-2", Amount: "5000.10", Status: "active", StartDate: "2024-02-29"},
				{ID: 10, LoanNumber: "L-10", Amount: "7500.123456789012345678", Status: "defaulted", StartDate: "2024-10-01"},
				{ID: 12, LoanNumber: "L-12", Amount: "600000.00", Status: "paid_off", StartDate: "2025-01-09"},
			},
			status: http.StatusOK,
			body: `{"customer_id":2147483647,"loans":[
				{"id":2,"loan_number":"L-2","amount":"5000.10","status":"active","start_date":"2024-02-29"},
				{"id":10,"loan_number":"L-10","amount":"7500.123456789012345678","status":"defaulted","start_date":"2024-10-01"},
				{"id":12,"loan_number":"L-12","amount":"600000.00","status":"paid_off","start_date":"2025-01-09"}
			]}`,
		},
		{
			name: "storage error is opaque", pathID: "42", customerID: 42,
			err: errors.New("internal database error for a restricted table"), status: http.StatusInternalServerError,
			body: `{"error":"internal_error"}`,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			calls := 0
			store := loanReaderFunc(func(ctx context.Context, gotID int32) ([]loan, error) {
				calls++
				if gotID != tt.customerID {
					t.Errorf("customer ID = %d, want %d", gotID, tt.customerID)
				}
				if _, ok := ctx.Deadline(); !ok {
					t.Error("query context has no deadline")
				}
				return tt.loans, tt.err
			})
			res := httptest.NewRecorder()
			newHandler(store, time.Second).ServeHTTP(res, httptest.NewRequest(http.MethodGet, customerPath+tt.pathID+"/loans", nil))
			assertJSON(t, res, tt.status, tt.body)
			if calls != 1 {
				t.Errorf("loan reader called %d times, want 1", calls)
			}
			if got := res.Header().Get("Cache-Control"); got != "no-store" {
				t.Errorf("Cache-Control = %q, want no-store", got)
			}
		})
	}
}

func TestQueryCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	store := loanReaderFunc(func(ctx context.Context, _ int32) ([]loan, error) {
		if !errors.Is(ctx.Err(), context.Canceled) {
			t.Errorf("query context error = %v, want context.Canceled", ctx.Err())
		}
		return nil, ctx.Err()
	})
	res := httptest.NewRecorder()
	req := httptest.NewRequestWithContext(ctx, http.MethodGet, customerPath+"42/loans", nil)
	newHandler(store, time.Second).ServeHTTP(res, req)
	assertJSON(t, res, http.StatusInternalServerError, `{"error":"internal_error"}`)
}

func TestRoutes(t *testing.T) {
	handler := newHandler(nil, time.Second)
	res := httptest.NewRecorder()
	handler.ServeHTTP(res, httptest.NewRequest(http.MethodGet, "/health", nil))
	assertJSON(t, res, http.StatusOK, `{"status":"ok"}`)

	for _, path := range []string{"/health", customerPath + "1/loans"} {
		t.Run(path, func(t *testing.T) {
			res := httptest.NewRecorder()
			handler.ServeHTTP(res, httptest.NewRequest(http.MethodPost, path, nil))
			if res.Code != http.StatusMethodNotAllowed {
				t.Errorf("POST %s returned %d, want 405", path, res.Code)
			}
		})
	}
}

func assertJSON(t *testing.T, res *httptest.ResponseRecorder, status int, body string) {
	t.Helper()
	if res.Code != status {
		t.Errorf("status = %d, want %d", res.Code, status)
	}
	if got := res.Header().Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	var got, want any
	if err := json.Unmarshal(res.Body.Bytes(), &got); err != nil {
		t.Fatalf("response is not JSON: %v", err)
	}
	if err := json.Unmarshal([]byte(body), &want); err != nil {
		t.Fatalf("invalid test expectation: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("JSON = %s, want %s", res.Body.String(), body)
	}
}
