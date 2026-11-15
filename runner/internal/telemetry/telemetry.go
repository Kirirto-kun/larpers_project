package telemetry

import (
	"bytes"
	"sync"
	"time"
)

type LogBuffer struct {
	mu       sync.Mutex
	limit    int
	data     []byte
	pending  []byte
	secrets  [][]byte
	dropped  int64
	accepted int
}

func NewLogBuffer(limit int, secrets []string) *LogBuffer {
	b := &LogBuffer{limit: limit}
	for _, s := range secrets {
		if s != "" {
			b.secrets = append(b.secrets, []byte(s))
		}
	}
	return b
}
func (b *LogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	b.pending = append(b.pending, p...)
	for {
		idx := bytes.IndexByte(b.pending, '\n')
		if idx < 0 {
			break
		}
		line := b.pending[:idx+1]
		b.appendRedacted(line)
		b.pending = b.pending[idx+1:]
	}
	if len(b.pending) > 65536 {
		b.appendRedacted(b.pending[:65536])
		b.dropped += int64(len(b.pending) - 65536)
		b.pending = nil
	}
	return n, nil
}
func (b *LogBuffer) appendRedacted(p []byte) {
	out := p
	for _, secret := range b.secrets {
		out = bytes.ReplaceAll(out, secret, []byte("[redacted]"))
	}
	remaining := max(0, b.limit-b.accepted)
	b.data = append(b.data, out[:min(len(out), remaining)]...)
	b.accepted += min(len(out), remaining)
	b.dropped += int64(max(0, len(out)-remaining))
}
func (b *LogBuffer) Bytes() []byte {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.pending) > 0 {
		b.appendRedacted(b.pending)
		b.pending = nil
	}
	return bytes.Clone(b.data)
}
func (b *LogBuffer) Drain() []byte {
	b.mu.Lock()
	defer b.mu.Unlock()
	data := bytes.Clone(b.data)
	b.data = nil
	return data
}
func (b *LogBuffer) Dropped() int64 { b.mu.Lock(); defer b.mu.Unlock(); return b.dropped }

type Sample struct {
	Sequence    int64          `json:"sequence"`
	Kind        string         `json:"kind"`
	WindowStart string         `json:"window_start_at"`
	WindowEnd   string         `json:"window_end_at"`
	DurationMS  int64          `json:"duration_ms"`
	Values      map[string]any `json:"values"`
}

func NewSample(sequence int64, kind string, start, end time.Time, values map[string]any) Sample {
	return Sample{Sequence: sequence, Kind: kind, WindowStart: start.UTC().Format(time.RFC3339Nano), WindowEnd: end.UTC().Format(time.RFC3339Nano), DurationMS: end.Sub(start).Milliseconds(), Values: values}
}
