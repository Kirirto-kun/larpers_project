package main

import (
	"campus/runner/internal/app"
	"context"
	"os"
	"os/signal"
)

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	os.Exit(app.Execute(ctx, os.Args[1:], os.Stdout, os.Stderr))
}
