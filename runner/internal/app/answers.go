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
	"unicode/utf8"
)

type previewAnswer struct {
	QuestionID string `json:"question_id"`
	Value      string `json:"answer"`
}
type answerRequest struct {
	Revision   json.RawMessage `json:"plan_revision"`
	QuestionID string          `json:"question_id"`
	Answer     string          `json:"answer"`
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
			QuestionID string          `json:"question_id"`
			Answer     string          `json:"answer"`
			Answers    []previewAnswer `json:"answers"`
		}
		if len(raw) > 0 && raw[0] == '[' {
			if err := json.Unmarshal(raw, &answers); err != nil {
				return nil, usageError("answers must contain {question_id,answer} entries")
			}
		} else {
			if err := json.Unmarshal(raw, &object); err != nil {
				return nil, usageError("answers file must contain {\"question_id\":...,\"answer\":\"...\"}")
			}
			answers = object.Answers
			if object.QuestionID != "" || object.Answer != "" {
				answers = append(answers, previewAnswer{QuestionID: object.QuestionID, Value: object.Answer})
			}
		}
	}
	for _, value := range values {
		id, text, ok := strings.Cut(value, "=")
		if !ok {
			return nil, usageError("--answer requires question_id=value")
		}
		answers = append(answers, previewAnswer{QuestionID: id, Value: text})
	}
	if len(answers) != 1 {
		return nil, usageError("the current preview accepts exactly one explicit string answer")
	}
	if err := validateAnswers(answerRequest{Revision: json.RawMessage(`1`), QuestionID: answers[0].QuestionID, Answer: answers[0].Value}); err != nil {
		return nil, err
	}
	return answers, nil
}

func validateAnswers(request answerRequest) error {
	if !validRevision(request.Revision) || !client.IDPattern.MatchString(request.QuestionID) || len(request.QuestionID) > 64 || utf8.RuneCountInString(request.Answer) < 1 || utf8.RuneCountInString(request.Answer) > 2000 {
		return usageError("answer requires a positive revision, valid question_id and 1..2000 characters of explicit text")
	}
	return nil
}

func (a *App) sendAnswers(ctx context.Context, c *client.Client, state *submission, path string) (preview, error) {
	if state.SubmissionStarted || state.JobID != "" {
		return preview{}, errors.New("answers cannot change a submitted job")
	}
	return sendPreviewAnswers(ctx, c, state.PreviewID, state.PendingAnswers, func(pending *pendingAnswers, revision json.RawMessage) error {
		state.PendingAnswers = pending
		state.Revision = revision
		return session.AtomicJSON(path, state)
	})
}

type saveAnswerState func(*pendingAnswers, json.RawMessage) error

func sendPreviewAnswers(ctx context.Context, c *client.Client, previewID string, pending *pendingAnswers, save saveAnswerState) (preview, error) {
	var result preview
	if pending == nil || !validID(previewID) || !validID(pending.Key) {
		return result, errors.New("answers require a valid saved preview and pending explicit answer")
	}
	if err := validateAnswers(pending.Request); err != nil {
		return result, err
	}
	err := c.DoOnce(ctx, "POST", "/job-previews/"+previewID+"/answers", pending.Request, &result, pending.Key)
	if err != nil {
		var rejected *client.APIError
		if errors.As(err, &rejected) && rejected.Status >= 400 && rejected.Status < 500 && rejected.Status != 429 {
			if saveErr := save(nil, nil); saveErr != nil {
				return result, fmt.Errorf("answer was rejected but saving recovery state failed: %w", saveErr)
			}
			return result, fmt.Errorf("preview answers rejected; correct the explicit values and resume the same preview: %w", err)
		}
		return result, fmt.Errorf("preview answer acknowledgement unknown; run campus run --resume to inspect the same preview without repeating the answer: %w", err)
	}
	if result.ID != "" && result.ID != previewID {
		return result, errors.New("answer receipt changed the preview_id; saved answers remain resumable")
	}
	if result.ID == "" || result.State == "" || !validRevision(result.Revision) {
		return reconcilePreviewAnswers(ctx, c, previewID, pending, save)
	}
	var before, after int64
	json.Unmarshal(pending.Request.Revision, &before)
	json.Unmarshal(result.Revision, &after)
	if !terminalPreviewState(result.State) && after <= before {
		return result, errors.New("answer acknowledgement remains unknown at the saved revision; no answer was repeated; resume later")
	}
	if err = save(nil, result.Revision); err != nil {
		return result, err
	}
	return result, nil
}

func (a *App) reconcileAnswers(ctx context.Context, c *client.Client, state *submission, path string) (preview, error) {
	return reconcilePreviewAnswers(ctx, c, state.PreviewID, state.PendingAnswers, func(pending *pendingAnswers, revision json.RawMessage) error {
		state.PendingAnswers = pending
		state.Revision = revision
		return session.AtomicJSON(path, state)
	})
}

func reconcilePreviewAnswers(ctx context.Context, c *client.Client, previewID string, pending *pendingAnswers, save saveAnswerState) (preview, error) {
	var result preview
	if pending == nil || !validID(previewID) {
		return result, errors.New("invalid saved answer reconciliation state")
	}
	if err := c.Do(ctx, "GET", "/job-previews/"+previewID, nil, &result, ""); err != nil {
		return result, err
	}
	if result.ID != previewID {
		return result, errors.New("coordinator returned a mismatched preview while reconciling the answer")
	}
	var before, after int64
	json.Unmarshal(pending.Request.Revision, &before)
	json.Unmarshal(result.Revision, &after)
	if !terminalPreviewState(result.State) && (!validRevision(result.Revision) || after <= before) {
		return result, errors.New("answer acknowledgement remains unknown at the saved revision; no answer was repeated; resume later or discard this never-submitted preview")
	}
	if err := save(nil, result.Revision); err != nil {
		return result, err
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
