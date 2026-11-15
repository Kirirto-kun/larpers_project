package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/discovery"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"
)

var errManagedLocalStop = errors.New("local participation ended")

type managedChild struct {
	settings client.DeviceSettings
	cancel   context.CancelFunc
	done     chan error
	control  *worker.ManagedControl
}

func (a *App) serveManagedLocked(parent context.Context, cfg config, identity session.Identity, profilesPath string, gpu bool) error {
	if cfg.Endpoint != "" && !sameCoordinator(cfg.Endpoint, identity.Endpoint) {
		return errors.New("saved device identity belongs to a different endpoint")
	}
	// Keep the same approved profile snapshot for every remote restart.
	profiles, _, err := readProfiles(profilesPath)
	if err != nil {
		return err
	}
	snapshot, err := session.PrivateTempDir(cfg.StateDir, ".managed-profiles-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(snapshot)
	profilesPath = filepath.Join(snapshot, "profiles.json")
	if err = session.AtomicJSON(profilesPath, profiles); err != nil {
		return err
	}
	c, err := apiClient(cfg, identity.Endpoint, identity.Credential, "")
	if err != nil {
		return err
	}
	c.Managed = true
	ctx, cancel := context.WithCancelCause(parent)
	defer cancel(context.Canceled)
	// Only the explicit local invocation clears an old leave request. Child
	// sessions must never write resume, even after a remote pause or reconnect.
	if err = session.Control(cfg.StateDir, "resume"); err != nil {
		return err
	}
	var localDrain atomic.Bool
	monitorDone := make(chan struct{})
	defer func() { cancel(context.Canceled); <-monitorDone }()
	go func() {
		defer close(monitorDone)
		tick := time.NewTicker(50 * time.Millisecond)
		defer tick.Stop()
		for {
			action, e := session.ReadControl(cfg.StateDir)
			if e != nil {
				cancel(e)
				return
			}
			if action == "stop" {
				cancel(errManagedLocalStop)
				return
			}
			if action == "drain" {
				localDrain.Store(true)
			}
			select {
			case <-ctx.Done():
				return
			case <-tick.C:
			}
		}
	}()
	type settingsResult struct {
		settings client.DeviceSettings
		err      error
	}
	settings := make(chan settingsResult)
	go func() {
		for {
			bounded, close := context.WithTimeout(ctx, 5*time.Second)
			value, e := c.DeviceSettings(bounded)
			close()
			select {
			case settings <- settingsResult{value, e}:
			case <-ctx.Done():
				return
			}
			timer := time.NewTimer(a.ManagedPollInterval)
			select {
			case <-timer.C:
			case <-ctx.Done():
				timer.Stop()
				return
			}
		}
	}()
	var child *managedChild
	stopChild := func() {
		if child != nil {
			child.cancel()
			<-child.done
			child = nil
		}
	}
	defer stopChild()
	var latest client.DeviceSettings
	var acknowledged client.SettingsAck
	var drainedRevision int64
	ack := func(s client.DeviceSettings, state, message string) {
		value := client.SettingsAck{Revision: s.Revision, Mode: s.Mode, ActualState: state, Error: message}
		if value == acknowledged {
			return
		}
		bounded, close := context.WithTimeout(ctx, 5*time.Second)
		e := c.AckSettings(bounded, value)
		close()
		if e == nil {
			acknowledged = value
		}
	}
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	fmt.Fprintln(a.Stdout, "Web controls enabled; keep this window open. Ctrl+C or campus leave ends this invocation.")
	for {
		var done <-chan error
		if child != nil {
			done = child.done
		}
		select {
		case <-ctx.Done():
			stopChild()
			if errors.Is(context.Cause(ctx), errManagedLocalStop) {
				return nil
			}
			return context.Cause(ctx)
		case <-tick.C:
			if localDrain.Load() {
				if child == nil {
					return nil
				}
				child.control.Drain()
			}
		case e := <-done:
			finished := child
			child = nil
			finished.cancel()
			if localDrain.Load() {
				return nil
			}
			if latest.DesiredState == "draining" {
				drainedRevision = latest.Revision
				ack(latest, "paused", "")
			} else if e != nil && ctx.Err() == nil {
				ack(finished.settings, "paused", "Local preflight or worker session failed; retrying safely.")
			}
		case result := <-settings:
			if localDrain.Load() {
				if child == nil {
					return nil
				}
				child.control.Drain()
				continue
			}
			if result.err != nil {
				stopChild() // Offline and old servers cannot keep claiming at stale settings.
				continue
			}
			s := result.settings
			if s.Revision < latest.Revision {
				continue
			}
			if s.Revision == latest.Revision && s != latest {
				stopChild()
				continue
			}
			latest = s
			if s.DesiredState == "paused" {
				stopChild()
				ack(s, "paused", "")
				continue
			}
			if s.DesiredState == "draining" {
				if child != nil {
					child.control.Drain()
				} else {
					drainedRevision = s.Revision
					ack(s, "paused", "")
				}
				continue
			}
			if s.Revision <= drainedRevision {
				continue
			}
			if child != nil && child.settings == s {
				continue
			}
			stopChild() // Includes quota reductions: teardown precedes new capabilities.
			if ctx.Err() != nil || localDrain.Load() {
				continue
			}
			childCtx, close := context.WithCancel(ctx)
			control := &worker.ManagedControl{Mode: s.Mode}
			control.OnReady = func(readyCtx context.Context) error {
				if localDrain.Load() {
					return errManagedLocalStop
				}
				return c.AckSettings(readyCtx, client.SettingsAck{Revision: s.Revision, Mode: s.Mode, ActualState: "running"})
			}
			current := &managedChild{settings: s, cancel: close, done: make(chan error, 1), control: control}
			child = current
			go func() { current.done <- a.runManagedSession(childCtx, cfg, identity, profilesPath, gpu, c, control) }()
		}
	}
}

func (a *App) runManagedSession(ctx context.Context, cfg config, identity session.Identity, path string, gpu bool, c *client.Client, control *worker.ManagedControl) error {
	if e := c.Handshake(ctx); e != nil {
		return e
	}
	caps, allowed, e := a.preflightWithCanonical(ctx, path, gpu, true)
	if e != nil {
		return e
	}
	caps.OfferedBudget, e = discovery.ProfileBudget(caps, gpu, control.Mode)
	if e != nil {
		return e
	}
	if caps.OfferedBudget.CPUMillis <= 0 || caps.OfferedBudget.MemoryBytes < 32<<20 {
		return errors.New("managed contribution paused: available CPU, RAM or Docker capacity unavailable")
	}
	// Re-read the saved session rather than overwriting changes from the previous
	// child with the supervisor's original identity snapshot.
	latest, e := loadIdentity(cfg.StateDir)
	if e != nil {
		return e
	}
	if latest.InstallationID != identity.InstallationID || latest.NodeID != identity.NodeID || latest.Credential != identity.Credential {
		return errors.New("local device identity changed during managed participation")
	}
	device := *c
	device.SessionID = ""
	return a.servePrepared(ctx, cfg, latest, &device, caps, allowed, gpu, control)
}
