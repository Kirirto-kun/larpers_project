package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"context"
	"errors"
	"time"
)

var errOwnerStop = errors.New("owner stopped participation")

func (r *Runner) monitorOwnerStop(ctx context.Context, cancel context.CancelCauseFunc) {
	changes := session.ControlChanges(ctx, r.StateDir)
	for {
		action, e := session.ReadControl(r.StateDir)
		if e != nil {
			cancel(e)
			return
		}
		if action == "stop" {
			cancel(errOwnerStop)
			return
		}
		r.controlAction.Store(action)
		select {
		case <-ctx.Done():
			return
		case _, ok := <-changes:
			if !ok {
				if ctx.Err() != nil {
					return
				}
				changes = session.ControlChanges(ctx, r.StateDir)
			}
		}
	}
}
func (r *Runner) pollAssignment(ctx context.Context, caps client.Capabilities) (*client.Assignment, error) {
	pollCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-pollCtx.Done():
				return
			case <-ticker.C:
				action, _ := r.controlAction.Load().(string)
				if action == "drain" || action == "stop" {
					cancel()
					return
				}
			}
		}
	}()
	a, e := r.Client.ClaimWork(pollCtx, 25000)
	cancel()
	<-done
	return a, e
}
func (r *Runner) releaseUnstarted(ctx context.Context, a client.Assignment, _ string) {
	if !client.IDPattern.MatchString(a.AttemptID) {
		return
	}
	bounded, cancel := context.WithTimeout(ctx, 250*time.Millisecond)
	defer cancel()
	r.Client.DoOnce(bounded, "POST", "/attempts/"+a.AttemptID+"/release", a.Fence.Proof(), nil, "")
}
