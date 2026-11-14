package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strings"
)

type previewAnswer struct {
	QuestionID string          `json:"question_id"`
	Value      json.RawMessage `json:"value"`
}
type answerRequest struct {
	Revision json.RawMessage `json:"plan_revision"`
	Answers  []previewAnswer `json:"answers"`
}
type pendingAnswers struct {
	Request answerRequest `json:"request"`
	Key     string        `json:"idempotency_key"`
}

func explicitAnswers(file string, values []string) ([]previewAnswer, error) {
	var answers []previewAnswer
	if file != "" {
		var raw json.RawMessage
		if err := readJSON(file, 64<<10, &raw); err != nil {
			return nil, usageError("read answers JSON: " + err.Error())
		}
		var object struct {
			Answers []previewAnswer `json:"answers"`
		}
		if len(raw) > 0 && raw[0] == '[' {
			if err := json.Unmarshal(raw, &answers); err != nil {
				return nil, usageError("answers must contain {question_id,value} entries")
			}
		} else {
			if err := json.Unmarshal(raw, &object); err != nil {
				return nil, usageError("answers file must contain {\"answers\":[{\"question_id\":...,\"value\":...}]}")
			}
			answers = object.Answers
		}
	}
	for _, value := range values {
		id, text, ok := strings.Cut(value, "=")
		if !ok {
			return nil, usageError("--answer requires question_id=value; values are strings, use --answers for typed JSON")
		}
		raw, _ := json.Marshal(text)
		answers = append(answers, previewAnswer{QuestionID: id, Value: raw})
	}
	if err := validateAnswers(answerRequest{Revision: json.RawMessage(`0`), Answers: answers}); err != nil {
		return nil, err
	}
	return answers, nil
}

func validateAnswers(request answerRequest) error {
	if !validRevision(request.Revision) || len(request.Answers) < 1 || len(request.Answers) > 100 {
		return usageError("answers require a valid revision and 1..100 explicit entries")
	}
	seen := map[string]bool{}
	for _, answer := range request.Answers {
		if !client.IDPattern.MatchString(answer.QuestionID) || seen[answer.QuestionID] || len(answer.Value) == 0 {
			return usageError("answers require unique valid question_id values and explicit JSON values")
		}
		seen[answer.QuestionID] = true
	}
	raw, err := json.Marshal(request)
	if err != nil {
		return usageError("answers contain invalid JSON")
	}
	if len(raw) > 64<<10 {
		return usageError("answer request exceeds 64 KiB")
	}
	var value any
	// Decoding into float64 rejects JSON numbers outside the finite range.
	if err = json.Unmarshal(raw, &value); err != nil {
		return usageError("answers require finite JSON values")
	}
	if !boundedJSONDepth(value, 0) {
		return usageError("answer request JSON nesting exceeds 16 levels")
	}
	return nil
}
func boundedJSONDepth(value any, depth int) bool {
	if depth > 16 {
		return false
	}
	switch typed := value.(type) {
	case []any:
		for _, child := range typed {
			if !boundedJSONDepth(child, depth+1) {
				return false
			}
		}
	case map[string]any:
		for _, child := range typed {
			if !boundedJSONDepth(child, depth+1) {
				return false
			}
		}
	}
	return true
}

func (a *App) sendAnswers(ctx context.Context, c *client.Client, state *submission, path string) (preview, error) {
	var result preview
	pending := state.PendingAnswers
	if pending == nil || !validID(state.PreviewID) || !validID(pending.Key) || state.SubmissionStarted || state.JobID != "" {
		return result, errors.New("answers cannot change a submitted job or an invalid saved preview")
	}
	if err := validateAnswers(pending.Request); err != nil {
		return result, err
	}
	err := c.Do(ctx, "POST", "/job-previews/"+state.PreviewID+"/answers", pending.Request, &result, pending.Key)
	if err != nil {
		var rejected *client.APIError
		if errors.As(err, &rejected) && rejected.Status >= 400 && rejected.Status < 500 && rejected.Status != 429 {
			state.PendingAnswers = nil
			state.Revision = nil
			if saveErr := session.AtomicJSON(path, state); saveErr != nil {
				return result, fmt.Errorf("answer was rejected but saving recovery state failed: %w", saveErr)
			}
			return result, fmt.Errorf("preview answers rejected; correct the explicit values and resume the same preview: %w", err)
		}
		return result, fmt.Errorf("preview answer acknowledgement unknown or rejected; run campus run --resume to replay the saved answers/key: %w", err)
	}
	if result.ID != "" && result.ID != state.PreviewID {
		return result, errors.New("answer receipt changed the preview_id; saved answers remain resumable")
	}
	state.PendingAnswers = nil
	state.Revision = nil
	if validRevision(result.Revision) {
		state.Revision = result.Revision
	}
	if err = session.AtomicJSON(path, state); err != nil {
		return result, err
	}
	if result.ID == "" || result.State == "" {
		return preview{}, nil
	}
	return result, nil
}

func terminalPreviewState(state string) bool {
	return state == "failed" || state == "cancelled" || state == "expired" || state == "rejected" || state == "discarded"
}

func (a *App) discardPreview(cfg config) error {
	unlock, err := lockState(filepath.Join(cfg.StateDir, "submit"))
	if err != nil {
		return err
	}
	defer unlock()
	path := filepath.Join(cfg.StateDir, "submission.json")
	var state submission
	if err = readJSON(path, 8<<20, &state); err != nil {
		return fmt.Errorf("no pending preview to discard: %w", err)
	}
	if state.SubmissionStarted || state.JobID != "" {
		return errors.New("cannot discard after job submission started; use campus run --resume to reconcile the saved job request")
	}
	state.PreviewTerminal = true
	state.TerminalState = "discarded"
	state.PendingAnswers = nil
	if err = session.AtomicJSON(path, state); err != nil {
		return err
	}
	fmt.Fprintln(a.Stdout, "Discarded the local never-submitted preview; a new run may now be created.")
	return nil
}
