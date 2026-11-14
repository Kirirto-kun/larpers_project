package leaseguard

import (
	"campus/runner/internal/session"
	"encoding/json"
	"errors"
	"io"
	"path/filepath"
	"sync"
	"time"
)

const Safety = 250 * time.Millisecond

type Lease struct {
	mu             sync.Mutex
	deadline, hard time.Time
	sequence       int64
}

func New(remainingMS, taskMS int64, rtt time.Duration, now time.Time) (*Lease, error) {
	l := &Lease{hard: now.Add(time.Duration(taskMS)*time.Millisecond - rtt - Safety)}
	if err := l.renew(remainingMS, taskMS, rtt, now); err != nil {
		return nil, err
	}
	return l, nil
}
func (l *Lease) renew(ms, taskMS int64, rtt time.Duration, now time.Time) error {
	ttl := time.Duration(ms)*time.Millisecond - rtt - Safety
	if ttl <= 0 || taskMS <= 0 {
		return errors.New("confirmed lease has insufficient remaining time")
	}
	hard := now.Add(time.Duration(taskMS)*time.Millisecond - rtt - Safety)
	if hard.Before(l.hard) {
		l.hard = hard
	}
	l.deadline = now.Add(ttl)
	if l.hard.Before(l.deadline) {
		l.deadline = l.hard
	}
	if !l.deadline.After(now) {
		return errors.New("task hard deadline expired")
	}
	l.sequence++
	return nil
}
func (l *Lease) Renew(ms, taskMS int64, rtt time.Duration, now time.Time) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.renew(ms, taskMS, rtt, now)
}
func (l *Lease) Remaining(now time.Time) time.Duration {
	l.mu.Lock()
	defer l.mu.Unlock()
	return max(0, l.deadline.Sub(now))
}
func (l *Lease) HardRemaining(now time.Time) time.Duration {
	l.mu.Lock()
	defer l.mu.Unlock()
	return max(0, l.hard.Sub(now))
}
func (l *Lease) Publish(state, control string, now time.Time) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	remaining := l.deadline.Sub(now)
	if remaining <= 0 {
		return errors.New("lease expired locally")
	}
	f, e := session.OpenSharedRead(filepath.Join(state, "challenge.json"))
	if e != nil {
		return e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, 4097))
	if e != nil {
		return e
	}
	if len(b) > 4096 {
		return errors.New("invalid guard challenge")
	}
	var challenge struct {
		Challenge string `json:"challenge"`
	}
	if e = json.Unmarshal(b, &challenge); e != nil {
		return e
	}
	if len(challenge.Challenge) < 8 || len(challenge.Challenge) > 256 {
		return errors.New("invalid guard challenge")
	}
	return session.AtomicJSON(filepath.Join(control, "lease.json"), map[string]any{"sequence": l.sequence, "challenge": challenge.Challenge, "remaining_ms": remaining.Milliseconds()})
}
