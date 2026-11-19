package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

type testGroup struct {
	status string
	count  int64
	amount string
}

type testRows struct {
	pgx.Rows
	groups []testGroup
	index  int
	err    error
	closed bool
}

func (r *testRows) Close() { r.closed = true }

func (r *testRows) Next() bool {
	if r.index == len(r.groups) {
		r.Close()
		return false
	}
	r.index++
	return true
}

func (r *testRows) Scan(dest ...any) error {
	group := r.groups[r.index-1]
	*dest[0].(*string) = group.status
	*dest[1].(*int64) = group.count
	*dest[2].(*string) = group.amount
	return nil
}

func (r *testRows) Err() error { return r.err }

type testQuery func(context.Context, string, ...any) (pgx.Rows, error)

func (q testQuery) Query(ctx context.Context, query string, args ...any) (pgx.Rows, error) {
	return q(ctx, query, args...)
}

func TestLoanSummary(t *testing.T) {
	tests := []struct {
		name   string
		groups []testGroup
		rowErr error
		code   int
		body   string
	}{
		{
			name:   "grupos y suma exacta",
			groups: []testGroup{{"alpha", 2, "9123456789012345.67"}, {"zeta", 3, "49.8"}},
			code:   http.StatusOK,
			body:   `{"total_loans":5,"by_status":[{"status":"alpha","count":2,"total_amount":"9123456789012345.67"},{"status":"zeta","count":3,"total_amount":"49.80"}]}`,
		},
		{
			name: "tabla vacía",
			code: http.StatusOK,
			body: `{"total_loans":0,"by_status":[]}`,
		},
		{
			name:   "escala incompatible",
			groups: []testGroup{{"alpha", 1, "10.001"}},
			code:   http.StatusInternalServerError,
			body:   `{"error":"no se pudo obtener el resumen"}`,
		},
		{
			name:   "error después de leer un grupo",
			groups: []testGroup{{"alpha", 1, "10.00"}},
			rowErr: errors.New("detalle interno de PostgreSQL"),
			code:   http.StatusInternalServerError,
			body:   `{"error":"no se pudo obtener el resumen"}`,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rows := &testRows{groups: tt.groups, err: tt.rowErr}
			store := loanStore{db: testQuery(func(ctx context.Context, query string, args ...any) (pgx.Rows, error) {
				if query != loanSummarySQL || len(args) != 0 {
					t.Fatalf("consulta inesperada: %q, %v", query, args)
				}
				if _, ok := ctx.Deadline(); !ok {
					t.Fatal("consulta sin límite de tiempo")
				}
				return rows, nil
			})}
			handler := newHandler(store, nil)
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/api/v1/loans/summary", nil))
			checkJSON(t, w, tt.code, tt.body)
			if !rows.closed {
				t.Error("las filas quedaron abiertas")
			}
		})
	}
}

func TestLoanSummaryDeadline(t *testing.T) {
	store := loanStore{db: testQuery(func(ctx context.Context, _ string, _ ...any) (pgx.Rows, error) {
		return nil, ctx.Err()
	})}
	ctx, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()

	handler := newHandler(store, nil)
	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/loans/summary", nil).WithContext(ctx)
	handler.ServeHTTP(w, req)
	checkJSON(t, w, http.StatusGatewayTimeout, `{"error":"consulta agotó el tiempo límite"}`)
}

func TestHealth(t *testing.T) {
	tests := []struct {
		name string
		err  error
		code int
		body string
	}{
		{"disponible", nil, http.StatusOK, `{"status":"ok"}`},
		{"no disponible", errors.New("detalle interno"), http.StatusServiceUnavailable, `{"status":"unavailable"}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			handler := newHandler(nil, func(ctx context.Context) error {
				if _, ok := ctx.Deadline(); !ok {
					t.Fatal("verificación sin límite de tiempo")
				}
				return tt.err
			})
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/health", nil))
			checkJSON(t, w, tt.code, tt.body)
		})
	}
}

func TestMethodNotAllowed(t *testing.T) {
	handler := newHandler(nil, nil)
	for _, path := range []string{"/health", "/api/v1/loans/summary"} {
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, httptest.NewRequest(http.MethodPost, path, nil))
		if w.Code != http.StatusMethodNotAllowed {
			t.Errorf("POST %s: código %d, esperaba 405", path, w.Code)
		}
	}
}

func TestFormatAmount(t *testing.T) {
	for input, want := range map[string]string{
		"0": "0.00", "-1.2": "-1.20", "9999999999999999.99": "9999999999999999.99",
	} {
		got, err := formatAmount(input)
		if err != nil || got != want {
			t.Errorf("formatAmount(%q) = %q, %v; esperaba %q", input, got, err, want)
		}
	}
	for _, input := range []string{"1.001", "1.000", "NaN", "Infinity", "1e3", "1.", ""} {
		if _, err := formatAmount(input); err == nil {
			t.Errorf("formatAmount(%q) debió devolver un error", input)
		}
	}
}

func checkJSON(t *testing.T, w *httptest.ResponseRecorder, code int, body string) {
	t.Helper()
	if w.Code != code || w.Header().Get("Content-Type") != "application/json" || w.Body.String() != body+"\n" {
		t.Fatalf("respuesta %d, tipo %q, cuerpo %q; esperaba %d y %q",
			w.Code, w.Header().Get("Content-Type"), w.Body.String(), code, body)
	}
}
