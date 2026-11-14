package client

import (
	"bytes"
	"campus/runner/internal/generated"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type APIError struct {
	Code        string `json:"code"`
	Message     string `json:"message"`
	FieldErrors any    `json:"field_errors"`
	Retryable   bool   `json:"retryable"`
	RequestID   string `json:"request_id"`
	Status      int    `json:"-"`
}

func (e *APIError) Error() string {
	return fmt.Sprintf("API %s (HTTP %d): %s", e.Code, e.Status, e.Message)
}
func Terminal(err error) bool {
	var e *APIError
	if !errors.As(err, &e) {
		return false
	}
	return e.Status == 401 || e.Status == 403 || e.Code == "STALE_ATTEMPT" || e.Code == "LEASE_EXPIRED" || e.Code == "SESSION_REPLACED"
}

// Attempt fencing only stops that attempt. Credential/session revocation stops
// the node, including its other active attempts.
func SessionFatal(err error) bool {
	var e *APIError
	if !errors.As(err, &e) {
		return false
	}
	if e.Code == "STALE_ATTEMPT" || e.Code == "LEASE_EXPIRED" {
		return false
	}
	return e.Status == 401 || e.Status == 403 || e.Code == "SESSION_REPLACED"
}

type Client struct {
	Managed        bool
	BaseURL        string
	Credential     string
	SessionID      string
	HTTP           *http.Client
	Attempts       int
	Backoff        time.Duration
	UploadPartSize int64
}

func New(endpoint, credential, session string, h *http.Client) (*Client, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/" && u.Path != "/api/v1" && u.Path != "/api/v1/") {
		return nil, errors.New("invalid coordinator endpoint")
	}
	if u.Scheme != "https" {
		if u.Scheme != "http" || (u.Hostname() != "127.0.0.1" && u.Hostname() != "localhost" && u.Hostname() != "::1") {
			return nil, errors.New("coordinator requires HTTPS (HTTP is only available on loopback)")
		}
	}
	if strings.ContainsAny(credential, "\r\n") || strings.ContainsAny(session, "\r\n") {
		return nil, errors.New("invalid credential/session")
	}
	if h == nil {
		h = &http.Client{Timeout: 40 * time.Second}
	}
	copyClient := *h
	copyClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	u.Path = "/api/v1"
	return &Client{BaseURL: strings.TrimSuffix(u.String(), "/"), Credential: credential, SessionID: session, HTTP: &copyClient, Attempts: 3, Backoff: 200 * time.Millisecond}, nil
}

func (c *Client) Request(ctx context.Context, method, route string, body io.Reader, key string) (*http.Request, error) {
	if !strings.HasPrefix(route, "/") || strings.HasPrefix(route, "//") || strings.Contains(route, "..") || strings.ContainsAny(route, "\r\n") {
		return nil, errors.New("unsafe API route")
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+route, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "campus/"+Version)
	if c.Managed {
		req.Header.Set("X-Campus-Worker-Protocol", "2")
	}
	if c.Credential != "" {
		req.Header.Set("Authorization", "Bearer "+c.Credential)
	}
	if c.SessionID != "" {
		req.Header.Set("X-Node-Session", c.SessionID)
	}
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	return req, nil
}

func responseError(resp *http.Response) error {
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 65537))
	e := &APIError{Status: resp.StatusCode, Code: http.StatusText(resp.StatusCode), Message: "request rejected"}
	if len(b) <= 65536 {
		json.Unmarshal(b, e)
	}
	e.Status = resp.StatusCode
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		e.Retryable = false
	}
	return e
}
func (c *Client) Wait(ctx context.Context, attempt int) error {
	d := c.Backoff * time.Duration(1<<min(attempt, 6))
	d += time.Duration(rand.Int64N(max(int64(d/2), 1)))
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (c *Client) do(ctx context.Context, method, route string, input, output any, key string, attempts int) error {
	var raw []byte
	var err error
	if input != nil {
		raw, err = json.Marshal(input)
		if err != nil {
			return err
		}
	}
	var last error
	for i := 0; i < max(1, attempts); i++ {
		req, err := c.Request(ctx, method, route, bytes.NewReader(raw), key)
		if err != nil {
			return err
		}
		// net/http otherwise silently replays a POST carrying Idempotency-Key on a
		// reused connection. Reconciliation/retry ordering belongs to this client.
		req.GetBody = nil
		if input != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		resp, err := c.HTTP.Do(req)
		if err == nil {
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				if output != nil && resp.StatusCode != 204 {
					b, e := io.ReadAll(io.LimitReader(resp.Body, 8<<20+1))
					resp.Body.Close()
					if e != nil {
						return e
					}
					if len(b) > 8<<20 {
						return errors.New("API response exceeds 8 MiB")
					}
					if err := json.Unmarshal(b, output); err != nil {
						return fmt.Errorf("malformed API response: %w", err)
					}
				} else {
					io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
					resp.Body.Close()
				}
				return nil
			}
			err = responseError(resp)
			var api *APIError
			if errors.As(err, &api) {
				if Terminal(err) || !(api.Status == 429 || api.Status >= 500) && !api.Retryable {
					return err
				}
				if api.Status >= 300 && api.Status < 400 {
					return err
				}
			}
		}
		last = err
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if i+1 < attempts {
			if err := c.Wait(ctx, i); err != nil {
				return err
			}
		}
	}
	return last
}
func (c *Client) Do(ctx context.Context, method, route string, input, output any, key string) error {
	return c.do(ctx, method, route, input, output, key, c.Attempts)
}

// DoOnce keeps mutations without a published idempotency guarantee from replay.
func (c *Client) DoOnce(ctx context.Context, method, route string, input, output any, key string) error {
	return c.do(ctx, method, route, input, output, key, 1)
}

func parseVersion(v string) ([3]int, error) {
	var result [3]int
	parts := strings.Split(v, ".")
	if len(parts) != 3 {
		return result, errors.New("invalid protocol version")
	}
	for i, p := range parts {
		n, e := strconv.Atoi(p)
		if e != nil || n < 0 {
			return result, errors.New("invalid protocol version")
		}
		result[i] = n
	}
	return result, nil
}
func compareVersion(a, b [3]int) int {
	for i := range a {
		if a[i] < b[i] {
			return -1
		}
		if a[i] > b[i] {
			return 1
		}
	}
	return 0
}
func (c *Client) Handshake(ctx context.Context) error {
	var p generated.Protocol
	if err := c.Do(ctx, "GET", "/protocol", nil, &p, ""); err != nil {
		return err
	}
	value := func(p *int64) int64 {
		if p == nil {
			return 1
		}
		return *p
	}
	if value(p.APIVersion) != 1 || value(p.MinWorkerVersion) != 1 || value(p.MaxWorkerVersion) != 1 || p.LeaseMs <= 0 || p.HeartbeatIntervalMs <= 0 || p.HeartbeatIntervalMs >= p.LeaseMs || p.LongPollMs < 0 {
		return errors.New("incompatible coordinator protocol version")
	}
	return nil
}

func (c *Client) Complete(ctx context.Context, fence Fence, result generated.ResultManifest) (Receipt, error) {
	route := "/attempts/" + url.PathEscape(fence.AttemptID)
	var receipt Receipt
	validReceipt := func(receipt Receipt, taskID string) error {
		_, err := time.Parse(time.RFC3339Nano, receipt.SubmittedAt)
		if receipt.AttemptID != fence.AttemptID || receipt.TaskID == "" || taskID != "" && receipt.TaskID != taskID || err != nil || receipt.State != generated.TaskStateVerifying && receipt.State != generated.TaskStateSucceeded {
			return errors.New("invalid completion receipt identity, state, or submission time")
		}
		return nil
	}
	body := generated.Complete{Generation: fence.Generation, LeaseToken: fence.LeaseToken, Manifest: result}
	// A complete request may have committed even if its response was lost. Reconcile
	// before ever retrying it. The same fenced body/key is used for any retry.
	for i := 0; i < max(1, c.Attempts); i++ {
		receipt = Receipt{}
		err := c.do(ctx, "POST", route+"/complete", body, &receipt, "complete:"+fence.AttemptID+":"+strconv.FormatInt(fence.Generation, 10), 1)
		if err == nil {
			if err := validReceipt(receipt, ""); err != nil {
				return Receipt{}, err
			}
			return receipt, nil
		}
		if Terminal(err) {
			return receipt, err
		}
		var api *APIError
		if errors.As(err, &api) && api.Status < 500 && api.Status != 429 {
			return receipt, err
		}
		var status generated.AttemptStatus
		if statusErr := c.Do(ctx, "GET", route, nil, &status, ""); statusErr != nil {
			return Receipt{}, errors.Join(err, statusErr)
		}
		if status.AttemptID != fence.AttemptID || status.Generation != fence.Generation || status.TaskID == "" {
			return Receipt{}, errors.New("completion reconciliation attempt/generation mismatch")
		}
		switch status.State {
		case generated.TaskStateVerifying, generated.TaskStateSucceeded:
			if status.Receipt == nil {
				return Receipt{}, errors.New("completion reconciliation has no receipt")
			}
			if err := validReceipt(*status.Receipt, status.TaskID); err != nil {
				return Receipt{}, err
			}
			return *status.Receipt, nil
		case generated.TaskStateLeased, generated.TaskStateRunning:
			if status.Receipt != nil {
				return Receipt{}, errors.New("active attempt unexpectedly has a completion receipt")
			}
		default:
			return Receipt{}, errors.New("attempt is no longer active or status is invalid")
		}
		if i+1 >= c.Attempts {
			return receipt, err
		}
		if err := c.Wait(ctx, i); err != nil {
			return receipt, err
		}
	}
	return receipt, errors.New("complete reconciliation exhausted")
}
