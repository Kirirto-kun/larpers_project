//go:build linux || darwin

package session

import (
	"context"
	"time"
)

func ControlChanges(ctx context.Context, dir string) <-chan struct{} {
	events := make(chan struct{}, 1)
	go func() {
		defer close(events)
		ticker := time.NewTicker(500 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				select {
				case events <- struct{}{}:
				default:
				}
			}
		}
	}()
	return events
}
