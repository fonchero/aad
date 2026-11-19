package main

import (
	"context"
	"log"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	// Read connection settings from PG* without loading local credential files.
	config, err := pgxpool.ParseConfig("passfile='' servicefile='' sslcert='' sslkey=''")
	if err != nil {
		log.Fatal("invalid PostgreSQL configuration")
	}
	if config.ConnConfig.ConnectTimeout <= 0 {
		config.ConnConfig.ConnectTimeout = queryTimeout
	}
	config.ConnConfig.RuntimeParams["default_transaction_read_only"] = "on"
	config.ConnConfig.RuntimeParams["statement_timeout"] = queryTimeout.String()

	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	if err != nil {
		log.Fatal("could not initialize PostgreSQL connection pool")
	}

	server := &http.Server{
		Addr:              ":8080",
		Handler:           newHandler(postgresStore{pool: pool}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Print("HTTP server listening on :8080")
	err = server.ListenAndServe()
	pool.Close()
	if err != http.ErrServerClosed {
		log.Fatal("HTTP server stopped unexpectedly")
	}
}
