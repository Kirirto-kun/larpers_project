package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"strings"
	"sync"
	"unicode/utf8"
)

type attemptLogSender struct {
	mu       sync.Mutex
	client   *client.Client
	fence    client.Fence
	sequence int64
}

func newAttemptLogSender(c *client.Client, fence client.Fence) *attemptLogSender {
	return &attemptLogSender{client: c, fence: fence}
}

// The caller supplies already redacted, lifetime-quota-limited log bytes.
func (s *attemptLogSender) Send(ctx context.Context, data []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	text := strings.ToValidUTF8(string(data), "\uFFFD")
	for len(text) > 0 {
		end := min(len(text), 8192)
		for end < len(text) && !utf8.RuneStart(text[end]) {
			end--
		}
		s.sequence++
		body := generated.LogWrite{Generation: s.fence.Generation, LeaseToken: s.fence.LeaseToken, Sequence: s.sequence, Text: text[:end]}
		if err := s.client.DoOnce(ctx, "POST", "/attempts/"+s.fence.AttemptID+"/logs", body, nil, ""); err != nil {
			return err
		}
		text = text[end:]
	}
	return nil
}
