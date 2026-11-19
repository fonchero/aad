package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	if err := run(); err != nil {
		log.Print(err)
		os.Exit(1)
	}
}

func run() error {
	if os.Getenv("PGSERVICE") != "" {
		return errors.New("configure PostgreSQL con variables PG* directas, sin PGSERVICE")
	}
	// Desactiva la lectura de archivos de contraseñas de libpq.
	config, err := pgxpool.ParseConfig("passfile='' default_transaction_read_only=on")
	if err != nil {
		return errors.New("configuración de PostgreSQL inválida")
	}
	if config.ConnConfig.ConnectTimeout == 0 {
		config.ConnConfig.ConnectTimeout = 5 * time.Second
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("no se pudo inicializar el pool de PostgreSQL")
	}
	defer pool.Close()

	server := &http.Server{
		Addr:              ":8080",
		Handler:           newHandler(loanStore{db: pool}, pool.Ping),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	stopped := make(chan error, 1)
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		err := server.Shutdown(shutdownCtx)
		if err != nil {
			_ = server.Close()
		}
		stopped <- err
	}()

	if err := server.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		return errors.New("no se pudo servir HTTP en :8080")
	}
	if err := <-stopped; err != nil {
		return errors.New("se agotó el plazo de cierre HTTP")
	}
	return nil
}
