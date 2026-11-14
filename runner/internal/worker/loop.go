package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/discovery"
	"campus/runner/internal/session"
	"context"
	"errors"
	"fmt"
	"math"
	"strconv"
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
	if r.ManagedControl == nil {
		if e := session.Control(r.StateDir, "resume"); e != nil {
			return e
		}
	} else {
		action, e := r.participationAction()
		if e != nil {
			return e
		}
		if action == "stop" || action == "drain" {
			return nil
		}
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
	interval := r.RefreshInterval
	if interval <= 0 {
		interval = 30 * time.Second
	}
	discover := r.Discover
	if discover == nil {
		discover = discovery.Discover
	}
	refresh := time.Now().Add(interval)
	// Discovery samples describe the host, not the server session's revision.
	// Advance for every new body, even after a failed/lost acknowledgement.
	preflightRevision, _ := strconv.ParseInt(capabilities.PreflightRevision, 10, 64)
	if preflightRevision < 1 {
		preflightRevision = 1
	}
	capabilityPublicationPending := false
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
	if r.OnReady != nil {
		if e := r.OnReady(runCtx); e != nil {
			return e
		}
	}
	for {
		if runCtx.Err() != nil {
			cancel()
			return runCtx.Err()
		}
		action, e := r.participationAction()
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
			updated, e := discover(runCtx)
			if e == nil {
				updated, e = r.refreshCapabilities(capabilities, updated, gpu)
				if e != nil {
					return e
				}
				if updated.PowerState == "battery" && !updated.AllowOnBattery {
					cancel()
					return errors.New("participation paused on battery power")
				}
				// Ambient free capacity includes this worker's own running tasks.
				// Preserve those bounded reservations and reduce future admission.
				budget.Constrain(updated.OfferedBudget)
				if preflightRevision == math.MaxInt64 {
					return errors.New("preflight revision exhausted")
				}
				preflightRevision++
				updated.PreflightRevision = strconv.FormatInt(preflightRevision, 10)
				wire, err := updated.Contract()
				if err != nil {
					return err
				}
				if e = r.Client.Do(runCtx, "PUT", "/node-sessions/"+r.Client.SessionID+"/capabilities", wire, nil, ""); e != nil {
					// The coordinator may still schedule using the old larger offer.
					// Do not consume attempt generations by claiming/releasing until
					// a fresh capability publication is acknowledged.
					capabilityPublicationPending = true
					if client.SessionFatal(e) {
						cancel()
						return e
					}
				} else {
					capabilityPublicationPending = false
					// Commit only acknowledged capabilities. Failed publication keeps
					// conservative admission and retries a freshly sampled revision.
					capabilities = updated
					budget.Update(updated.OfferedBudget)
				}
			}
			refresh = time.Now().Add(interval)
		}
		if action == "drain" || capabilityPublicationPending || len(slots) == cap(slots) {
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
			if action, _ := r.participationAction(); action == "drain" || action == "stop" {
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
		if action, _ := r.participationAction(); action == "drain" || action == "stop" {
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
		if action, _ := r.participationAction(); action == "drain" || action == "stop" {
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
