package leaseguard

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLeaseCannotBeExtendedByFailedOrStaleRenewal(t *testing.T) {
	now := time.Now()
	l, e := New(10000, 30000, 100*time.Millisecond, now)
	if e != nil {
		t.Fatal(e)
	}
	before := l.Remaining(now)
	if e = l.Renew(9000, 29000, 50*time.Millisecond, now.Add(time.Second)); e != nil {
		t.Fatal(e)
	}
	remaining := l.Remaining(now.Add(time.Second))
	if remaining >= 9*time.Second || before >= 10*time.Second {
		t.Fatal("RTT/safety not deducted")
	}
	if e = l.Renew(0, 29000, 0, now.Add(2*time.Second)); e == nil {
		t.Fatal("invalid renew accepted")
	}
	if l.Remaining(now.Add(2*time.Second)) >= remaining {
		t.Fatal("failed renew extended lease")
	}
}
func TestGuardGrantUsesFreshChallengeAndSequence(t *testing.T) {
	root := t.TempDir()
	state := filepath.Join(root, "state")
	control := filepath.Join(root, "control")
	os.MkdirAll(state, 0700)
	os.MkdirAll(control, 0700)
	os.WriteFile(filepath.Join(state, "challenge.json"), []byte(`{"challenge":"random-nonce"}`), 0600)
	l, _ := New(60000, 120000, 0, time.Now())
	if e := l.Publish(state, control, time.Now()); e != nil {
		t.Fatal(e)
	}
	var g map[string]any
	b, _ := os.ReadFile(filepath.Join(control, "lease.json"))
	json.Unmarshal(b, &g)
	if g["challenge"] != "random-nonce" || g["sequence"].(float64) != 1 {
		t.Fatal(g)
	}
	if e := l.Publish(state, control, time.Now()); e != nil {
		t.Fatal(e)
	}
	b, _ = os.ReadFile(filepath.Join(control, "lease.json"))
	var again map[string]any
	json.Unmarshal(b, &again)
	if again["sequence"] != g["sequence"] {
		t.Fatal("publishing without renew invented a lease sequence")
	}
}
