package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const queryTimeout = 5 * time.Second

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	config, err := postgresConfig()
	if err != nil {
		return err
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("no se pudo inicializar el pool PostgreSQL")
	}
	defer pool.Close()

	server := &http.Server{
		Addr:              ":8080",
		Handler:           newHandler(postgresLoanReader{pool: pool}, queryTimeout),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	done := make(chan error, 1)
	go func() {
		done <- server.ListenAndServe()
	}()

	select {
	case err := <-done:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return server.Shutdown(shutdownCtx)
	}
}

func postgresConfig() (*pgxpool.Config, error) {
	for _, name := range []string{"PGHOST", "PGDATABASE", "PGUSER"} {
		if os.Getenv(name) == "" {
			return nil, errors.New("falta la variable " + name)
		}
	}
	if os.Getenv("PGSERVICE") != "" {
		return nil, errors.New("configure PostgreSQL con variables PG* de conexión directa")
	}

	params := url.Values{
		"passfile":                      {""},
		"default_transaction_read_only": {"on"},
	}
	if os.Getenv("PGSSLMODE") == "" {
		params.Set("sslmode", "verify-full")
	}
	if os.Getenv("PGCONNECT_TIMEOUT") == "" {
		params.Set("connect_timeout", "5")
	}
	config, err := pgxpool.ParseConfig("postgresql://?" + params.Encode())
	if err != nil {
		return nil, errors.New("configuración PostgreSQL inválida")
	}
	return config, nil
}
