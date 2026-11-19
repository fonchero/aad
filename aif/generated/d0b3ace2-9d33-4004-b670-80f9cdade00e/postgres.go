package main

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

const customerLoansQuery = `
SELECT l.id, l.loan_number, l.amount::text, l.status, l.start_date
FROM customers AS c
LEFT JOIN loans AS l ON l.customer_id = c.id
WHERE c.id = $1
ORDER BY l.id ASC`

type postgresLoanReader struct {
	pool *pgxpool.Pool
}

func (s postgresLoanReader) listLoans(ctx context.Context, customerID int32) ([]loan, error) {
	rows, err := s.pool.Query(ctx, customerLoansQuery, customerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var found bool
	loans := []loan{}
	for rows.Next() {
		found = true
		var id *int32
		var loanNumber, amount, status *string
		var startDate pgtype.Date
		if err := rows.Scan(&id, &loanNumber, &amount, &status, &startDate); err != nil {
			return nil, err
		}
		// A NULL loan ID is the LEFT JOIN row for an existing customer without loans.
		if id == nil {
			continue
		}
		if loanNumber == nil || amount == nil || status == nil || !startDate.Valid ||
			startDate.InfinityModifier != pgtype.Finite || startDate.Time.Year() < 1 || startDate.Time.Year() > 9999 {
			return nil, errors.New("loan data cannot be represented in the response")
		}
		loans = append(loans, loan{
			ID:         *id,
			LoanNumber: *loanNumber,
			Amount:     *amount,
			Status:     *status,
			StartDate:  startDate.Time.Format(time.DateOnly),
		})
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if !found {
		return nil, errCustomerNotFound
	}
	return loans, nil
}
