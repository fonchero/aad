package main

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var errCustomerNotFound = errors.New("customer not found")

type loanStatistics struct {
	CustomerID     int32 `json:"customer_id"`
	TotalLoans     int64 `json:"total_loans"`
	ActiveLoans    int64 `json:"active_loans"`
	DefaultedLoans int64 `json:"defaulted_loans"`
	PaidOffLoans   int64 `json:"paid_off_loans"`
}

type statisticsStore interface {
	LoanStatistics(ctx context.Context, customerID int32) (loanStatistics, error)
}

const loanStatisticsQuery = `
SELECT c.id,
       count(l.customer_id),
       count(*) FILTER (WHERE l.status = 'active'),
       count(*) FILTER (WHERE l.status = 'defaulted'),
       count(*) FILTER (WHERE l.status = 'paid_off')
FROM public.customers AS c
LEFT JOIN public.loans AS l ON l.customer_id = c.id
WHERE c.id = $1
GROUP BY c.id
`

type postgresStore struct {
	pool *pgxpool.Pool
}

func (s postgresStore) LoanStatistics(ctx context.Context, customerID int32) (loanStatistics, error) {
	var stats loanStatistics
	err := s.pool.QueryRow(ctx, loanStatisticsQuery, customerID).Scan(
		&stats.CustomerID, &stats.TotalLoans, &stats.ActiveLoans,
		&stats.DefaultedLoans, &stats.PaidOffLoans,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return loanStatistics{}, errCustomerNotFound
	}
	return stats, err
}
