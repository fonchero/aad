package main

import (
	"context"
	"errors"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5"
)

type loanSummary struct {
	TotalLoans int64        `json:"total_loans"`
	ByStatus   []loanStatus `json:"by_status"`
}

type loanStatus struct {
	Status      string `json:"status"`
	Count       int64  `json:"count"`
	TotalAmount string `json:"total_amount"`
}

type loanStore struct {
	db interface {
		Query(context.Context, string, ...any) (pgx.Rows, error)
	}
}

const loanSummarySQL = `
SELECT status, COUNT(*), SUM(amount)::text
FROM public.loans
GROUP BY status
ORDER BY status
`

func (store loanStore) summary(ctx context.Context) (loanSummary, error) {
	rows, err := store.db.Query(ctx, loanSummarySQL)
	if err != nil {
		return loanSummary{}, err
	}
	defer rows.Close()

	result := loanSummary{ByStatus: []loanStatus{}}
	for rows.Next() {
		var group loanStatus
		var amount string
		if err := rows.Scan(&group.Status, &group.Count, &amount); err != nil {
			return loanSummary{}, err
		}
		group.TotalAmount, err = formatAmount(amount)
		if err != nil {
			return loanSummary{}, err
		}
		result.TotalLoans += group.Count
		result.ByStatus = append(result.ByStatus, group)
	}
	if err := rows.Err(); err != nil {
		return loanSummary{}, err
	}
	return result, nil
}

// AIF verificó que loans.amount es NUMERIC(14,2). SUM conserva la
// precisión y puede ampliar los dígitos enteros del resultado.
const amountScale = 2

var decimal = regexp.MustCompile(`^-?[0-9]+(?:\.[0-9]+)?$`)

func formatAmount(value string) (string, error) {
	integer, fraction, _ := strings.Cut(value, ".")
	if !decimal.MatchString(value) || len(fraction) > amountScale {
		return "", errors.New("importe incompatible con la escala verificada")
	}
	return integer + "." + fraction + strings.Repeat("0", amountScale-len(fraction)), nil
}
