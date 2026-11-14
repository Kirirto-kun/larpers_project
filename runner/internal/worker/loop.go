package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/discovery"
	"campus/runner/internal/session"
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

// Serve keeps at most two attempts in flight; all queues reserve one shared budget.
func (r *Runner) Serve(ctx context.Context, capabilities client.Capabilities, gpu bool) (serveErr error) {
	if e := r.Client.Handshake(ctx); e != nil {
		return e
	}
	cleaner, ok := r.runtime().(interface{ Cleanup(context.Context) error })
	if !ok {
		return errors.New("runtime does not support safe installation cleanup")
	}
	if e := cleaner.Cleanup(ctx); e != nil {
		return e
	}
	if e := session.Control(r.StateDir, "resume"); e != nil {
		return e
	}
	r.controlAction.Store("resume")
	budget := NewBudget(capabilities.OfferedBudget)
	runCtx, cancelCause := context.WithCancelCause(ctx)
	cancel := func() { cancelCause(context.Canceled) }
	defer cancel()
	go r.nodeMetrics(runCtx)
	var active sync.WaitGroup
	slots := make(chan struct{}, 2)
	completed := make(chan error, 2)
	refresh := time.Now().Add(30 * time.Second)
	defer func() {
		cancel()
		active.Wait()
		if errors.Is(serveErr, context.Canceled) {
			cause := context.Cause(runCtx)
			if errors.Is(cause, errOwnerStop) {
				serveErr = nil
			} else if cause != nil {
				serveErr = cause
			}
		}
	}()
	go r.monitorOwnerStop(runCtx, cancelCause)
	for {
		if runCtx.Err() != nil {
			cancel()
			return runCtx.Err()
		}
		action, e := session.ReadControl(r.StateDir)
		if e != nil {
			cancel()
			return e
		}
		if action == "stop" {
			cancel()
			return nil
		}
		if action == "drain" && len(slots) == 0 {
			return nil
		}
		if time.Now().After(refresh) {
			updated, e := discovery.Discover(runCtx)
			if e == nil {
				verified := map[string]bool{}
				for _, g := range capabilities.GPUs {
					verified[g.ID] = g.CUDAVerified
				}
				for i := range updated.GPUs {
					updated.GPUs[i].CUDAVerified = verified[updated.GPUs[i].ID]
				}
				updated.OfferedBudget = discovery.Budget(updated, gpu)
				updated.ImageDigests = append([]string{}, capabilities.ImageDigests...)
				updated.AllowOnBattery = capabilities.AllowOnBattery
				if updated.PowerState == "battery" {
					cancel()
					return errors.New("participation paused on battery power")
				}
				capabilities = updated
				budget.Update(updated.OfferedBudget)
				wire, err := updated.Contract()
				if err != nil {
					return err
				}
				if e = r.Client.Do(runCtx, "PUT", "/node-sessions/"+r.Client.SessionID+"/capabilities", wire, nil, ""); e != nil && client.SessionFatal(e) {
					cancel()
					return e
				}
			}
			refresh = time.Now().Add(30 * time.Second)
		}
		if action == "drain" || len(slots) == cap(slots) {
			select {
			case <-runCtx.Done():
				cancel()
				return runCtx.Err()
			case e := <-completed:
				if client.SessionFatal(e) {
					cancel()
					return e
				}
			case <-time.After(250 * time.Millisecond):
			}
			continue
		}
		sent := time.Now()
		var assignment *client.Assignment
		assignment, e = r.pollAssignment(runCtx, capabilities)
		if e != nil {
			if errors.Is(e, client.ErrWorkerContractIncomplete) {
				return e
			}
			if action, _ := session.ReadControl(r.StateDir); action == "drain" || action == "stop" {
				continue
			}
			if client.SessionFatal(e) {
				cancel()
				return e
			}
			if e = r.Client.Wait(runCtx, 1); e != nil {
				cancel()
				return e
			}
			continue
		}
		if assignment == nil {
			select {
			case <-runCtx.Done():
				cancel()
				return runCtx.Err()
			case <-time.After(250 * time.Millisecond):
			}
			continue
		}
		a := *assignment
		a.ReceivedAt = time.Now()
		a.LeaseRemainingMS -= time.Since(sent).Milliseconds()
		a.TaskRemainingMS -= time.Since(sent).Milliseconds()
		if action, _ := session.ReadControl(r.StateDir); action == "drain" || action == "stop" {
			r.releaseUnstarted(runCtx, a, action)
			continue
		}
		release, e := budget.Acquire(a.ResourceLimits)
		if e != nil {
			r.releaseUnstarted(runCtx, a, "local_capacity_unavailable")
			continue
		}
		if e = a.Validate(); e != nil {
			release()
			return fmt.Errorf("invalid server assignment: %w", e)
		}
		if action, _ := session.ReadControl(r.StateDir); action == "drain" || action == "stop" {
			release()
			r.releaseUnstarted(runCtx, a, action)
			continue
		}
		if _, e = r.requestLease(runCtx, a, "started"); e != nil {
			release()
			if client.SessionFatal(e) {
				cancel()
				return e
			}
			continue
		}
		slots <- struct{}{}
		active.Add(1)
		go func(a client.Assignment, release func()) {
			defer active.Done()
			defer release()
			defer func() { <-slots }()
			_, err := r.RunAssignment(runCtx, a)
			if err != nil && !client.Terminal(err) {
				reportCtx, close := context.WithTimeout(context.Background(), 5*time.Second)
				r.reportAttemptError(reportCtx, a, runCtx.Err() != nil)
				close()
			}
			select {
			case completed <- err:
			default:
			}
			if client.SessionFatal(err) {
				cancelCause(err)
			}
		}(a, release)
	}
}
