package telemetry

import (
	"bytes"
	"testing"
)

func TestLogFloodIsBoundedAndRedacted(t *testing.T) {
	b := NewLogBuffer(32, []string{"secret"})
	p := bytes.Repeat([]byte("secret: payload\n"), 100)
	n, e := b.Write(p)
	if e != nil || n != len(p) {
		t.Fatal(n, e)
	}
	got := b.Bytes()
	if len(got) > 32 || bytes.Contains(got, []byte("secret")) || b.Dropped() == 0 {
		t.Fatalf("unbounded or leaking logs: %q", got)
	}
}

func TestDrainingLogsDoesNotResetLifetimeQuota(t *testing.T) {
	b := NewLogBuffer(8, nil)
	b.Write([]byte("1234567\n"))
	if len(b.Drain()) != 8 {
		t.Fatal("missing initial log")
	}
	b.Write([]byte("abcdefg\n"))
	if len(b.Drain()) != 0 || b.Dropped() != 8 {
		t.Fatal("drain reset cumulative log quota")
	}
}
