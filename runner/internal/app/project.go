package app

import (
	"bufio"
	"campus/runner/internal/artifacts"
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

type projectPreviewRequest struct {
	Prompt            string                   `json:"prompt"`
	ProjectConfig     projectConfig            `json:"project_config"`
	ProjectArtifactID string                   `json:"project_artifact_id"`
	Inputs            []generated.InputBinding `json:"inputs"`
	SelectedFiles     []string                 `json:"selected_files"`
	ShareWithLLM      bool                     `json:"share_with_llm"`
}
type projectReview struct {
	SchemaVersion string   `json:"schema_version"`
	Fingerprint   string   `json:"fingerprint"`
	Account       string   `json:"account"`
	ShareWithLLM  bool     `json:"share_with_llm"`
	SelectedFiles []string `json:"selected_files"`
	Approved      bool     `json:"approved"`
}
type projectSubmission struct {
	Endpoint             string                `json:"endpoint"`
	Account              string                `json:"account"`
	Key                  string                `json:"idempotency_key"`
	PreviewKey           string                `json:"preview_key"`
	PreviewStarted       bool                  `json:"preview_started"`
	PreviewID            string                `json:"preview_id"`
	Revision             json.RawMessage       `json:"plan_revision,omitempty"`
	ProjectDigest        string                `json:"project_digest"`
	Fingerprint          string                `json:"fingerprint"`
	Request              projectPreviewRequest `json:"preview_request"`
	SubmissionStarted    bool                  `json:"submission_started"`
	Approved             bool                  `json:"approved"`
	JobID                string                `json:"job_id,omitempty"`
	TerminalState        string                `json:"terminal_state,omitempty"`
	TerminalErrorCode    string                `json:"terminal_error_code,omitempty"`
	TerminalErrorMessage string                `json:"terminal_error_message,omitempty"`
	PendingAnswers       *pendingAnswers       `json:"pending_answers,omitempty"`
}

func (a *App) run(ctx context.Context, cfg config, args []string) error {
	for _, arg := range args {
		name, _, _ := strings.Cut(arg, "=")
		switch name {
		case "--", "--gpu", "--data", "--output":
			return a.runCommand(ctx, cfg, args)
		}
	}
	// Prefer this project's saved state. A legacy explicit command can still
	// recover inside a directory that also has an unused campus.json.
	if len(args) > 0 {
		legacy := false
		projectOption := false
		for _, arg := range args {
			name, _, _ := strings.Cut(arg, "=")
			switch name {
			case "--resume", "--discard", "--answer", "--answers":
				legacy = true
			case "--project", "--yes", "--wait", "--to":
				projectOption = true
			}
		}
		if legacy && !projectOption {
			root, err := a.Cwd()
			if err != nil {
				return err
			}
			if _, stateErr := os.Stat(filepath.Join(projectStateDir(cfg.StateDir, root), "submission.json")); os.IsNotExist(stateErr) {
				_, legacyErr := os.Stat(filepath.Join(cfg.StateDir, "submission.json"))
				_, configErr := os.Stat(filepath.Join(root, "campus.json"))
				if legacyErr == nil || os.IsNotExist(configErr) {
					return a.runCommand(ctx, cfg, args)
				}
			}
		}
	}
	return a.runProject(ctx, cfg, args)
}

func projectStateDir(stateRoot, root string) string {
	return filepath.Join(stateRoot, "projects", fingerprint(filepath.Clean(root)))
}

func (a *App) projectDiscovery(root string) error {
	fmt.Fprintln(a.Stdout, "No campus.json found. Create a supported map/reduce project with declared data globs and literal stage commands.")
	fmt.Fprintln(a.Stdout, "Candidate entry points and data (inspect these before declaring how work splits):")
	entries, err := os.ReadDir(root)
	if err != nil {
		return err
	}
	count := 0
	for _, entry := range entries {
		if entry.Type()&os.ModeSymlink != 0 || strings.HasPrefix(entry.Name(), ".") {
			continue
		}
		ext := strings.ToLower(filepath.Ext(entry.Name()))
		if entry.IsDir() {
			if entry.Name() == "data" || entry.Name() == "dataset" {
				fmt.Fprintln(a.Stdout, " ", entry.Name()+"/")
				count++
			}
			continue
		}
		switch ext {
		case ".py", ".js", ".mjs", ".go", ".rs", ".csv", ".tsv", ".jsonl", ".npz", ".parquet":
			fmt.Fprintln(a.Stdout, " ", entry.Name())
			count++
		}
		if count >= 30 {
			break
		}
	}
	return usageError("campus.json is required for project runs; declare independent map work and dependent reduce stages, or use run -- PROGRAM [ARG...]")
}

func projectRules(root, stateRoot, to string, c projectConfig, data packaging.ProjectManifest) (packaging.Rules, error) {
	rules := packaging.Rules{MaxFiles: 10000, MaxBytes: 256 << 20, Exclude: []string{"results/", "outputs/", "state/"}}
	for _, file := range data.Files {
		rules.Exclude = append(rules.Exclude, file.Path)
	}
	for _, stage := range c.Stages {
		for _, output := range stage.Outputs {
			rules.Exclude = append(rules.Exclude, output.Path)
		}
	}
	for _, target := range []string{stateRoot, to} {
		if target == "" {
			continue
		}
		rel, err := filepath.Rel(root, target)
		if err != nil {
			continue
		}
		if rel == "." {
			return rules, usageError("project root must differ from the state and output directories")
		}
		if rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			rules.Exclude = append(rules.Exclude, filepath.ToSlash(rel)+"/")
		}
	}
	return rules, nil
}

func (a *App) projectInventory(c projectConfig, code, data packaging.ProjectManifest) {
	fmt.Fprintln(a.Stdout, "Project:", c.Name)
	fmt.Fprintf(a.Stdout, "Code snapshot: %d files, %d bytes\n", len(code.Files), code.TotalBytes)
	for _, file := range code.Files {
		fmt.Fprintf(a.Stdout, "  code %s (%d bytes)\n", file.Path, file.SizeBytes)
	}
	fmt.Fprintf(a.Stdout, "Declared data: %d files, %d bytes (uploaded separately)\n", len(data.Files), data.TotalBytes)
	for _, file := range data.Files {
		fmt.Fprintf(a.Stdout, "  data %s (%d bytes, sha256 %s)\n", file.Path, file.SizeBytes, file.Digest)
	}
	configJSON, _ := json.Marshal(c)
	fmt.Fprintln(a.Stdout, "Execution contract (including effective defaults):", string(configJSON))
	for _, stage := range c.Stages {
		tasks := 1
		if stage.Kind == "map" {
			tasks = 0
			for _, file := range data.Files {
				for _, pattern := range stage.Inputs {
					if packaging.MatchGlob(pattern, file.Path) {
						tasks++
						break
					}
				}
			}
		}
		fmt.Fprintf(a.Stdout, "  stage %s: %s, %d task(s), accelerator %s\n", stage.ID, stage.Kind, tasks, stage.Accelerator)
	}
}

func selectedCode(root string, code packaging.ProjectManifest, selected []string, share bool) error {
	if len(selected) > 16 || share != (len(selected) > 0) {
		return usageError("selected code requires explicit sharing consent and 1–16 files, or none")
	}
	index := map[string]packaging.File{}
	for _, file := range code.Files {
		index[file.Path] = file
	}
	seen := map[string]bool{}
	var total int64
	rooted, err := os.OpenRoot(root)
	if err != nil {
		return err
	}
	defer rooted.Close()
	for _, name := range selected {
		entry, ok := index[name]
		if !ok || seen[strings.ToLower(name)] {
			return usageError("selected code must name distinct files in the code inventory; dataset/private files cannot be shared")
		}
		if err = packaging.SafePath(name); err != nil {
			return usageError("unsafe selected code path")
		}
		seen[strings.ToLower(name)] = true
		total += entry.SizeBytes
		if entry.SizeBytes > 8000 || total > 24000 {
			return usageError("selected code exceeds the provider inspection limit (8000 bytes per file, 24000 total)")
		}
		f, e := rooted.Open(filepath.FromSlash(name))
		if e != nil {
			return e
		}
		raw, e := io.ReadAll(io.LimitReader(f, 8001))
		f.Close()
		if e != nil {
			return e
		}
		sum := sha256.Sum256(raw)
		if int64(len(raw)) != entry.SizeBytes || hex.EncodeToString(sum[:]) != entry.Digest {
			return errors.New("selected code changed during review")
		}
		if !utf8.Valid(raw) || bytesContainNUL(raw) {
			return usageError("selected code must be bounded UTF-8 text")
		}
	}
	return nil
}
func bytesContainNUL(raw []byte) bool {
	for _, b := range raw {
		if b == 0 {
			return true
		}
	}
	return false
}

func (a *App) projectSharing(reader *bufio.Reader, root string, code packaging.ProjectManifest) ([]string, bool, error) {
	fmt.Fprintln(a.Stdout, "Only explicitly selected bounded code text is sent to the LLM. Dataset bytes and private files are excluded.")
	fmt.Fprint(a.Stdout, "Code paths to share with the LLM (comma-separated exact paths, or none): ")
	line, err := readLine(reader, 8192)
	if err != nil {
		return nil, false, usageError("project review requires explicit code-sharing choices; run interactively first, then --yes for the saved unchanged review")
	}
	line = strings.TrimSpace(line)
	selected := []string{}
	if strings.EqualFold(line, "none") {
		return selected, false, nil
	}
	if line == "" {
		return nil, false, usageError("selected code choices are required; enter exact paths or none")
	}
	for _, part := range strings.Split(line, ",") {
		selected = append(selected, strings.TrimSpace(part))
	}
	sort.Strings(selected)
	if err = selectedCode(root, code, selected, true); err != nil {
		return nil, false, err
	}
	return selected, true, nil
}

func (a *App) confirmProject(reader *bufio.Reader) error {
	fmt.Fprint(a.Stdout, "Submit this reviewed plan? [yes/no]: ")
	line, err := readLine(reader, 16)
	if err != nil || !strings.EqualFold(strings.TrimSpace(line), "yes") {
		return usageError("plan was not approved; use run --resume to review the saved preview")
	}
	return nil
}

func snapshotData(root, destination string, entry packaging.File) error {
	if err := os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	rooted, err := os.OpenRoot(root)
	if err != nil {
		return err
	}
	defer rooted.Close()
	input, err := rooted.Open(filepath.FromSlash(entry.Path))
	if err != nil {
		return err
	}
	defer input.Close()
	output, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(output, h), io.LimitReader(input, entry.SizeBytes+1))
	closeErr := output.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if n != entry.SizeBytes || hex.EncodeToString(h.Sum(nil)) != entry.Digest {
		return errors.New("declared data changed during snapshot; review again")
	}
	return nil
}

func (a *App) runProject(ctx context.Context, cfg config, args []string) error {
	f := a.flags("run")
	project := f.String("project", "", "project directory containing campus.json (default current directory)")
	yes := f.Bool("yes", false, "reuse the saved explicit sharing and approved unchanged project review")
	resume := f.Bool("resume", false, "resume this project's frozen preview/submission")
	discard := f.Bool("discard", false, "discard a never-submitted project preview")
	wait := f.Bool("wait", false, "follow the submitted job until it succeeds or fails")
	to := f.String("to", "", "download verified outputs into a nonexisting directory; implies --wait")
	answersFile := f.String("answers", "", "explicit JSON answer for this project's saved preview (requires --resume)")
	var answerValues stringList
	f.Var(&answerValues, "answer", "question_id=value explicit string answer (requires --resume; repeatable)")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 {
		return usageError("project run takes flags; literal program argv follows --")
	}
	var answers []previewAnswer
	if *answersFile != "" || len(answerValues) > 0 {
		if !*resume {
			return usageError("--answers and --answer require run --resume")
		}
		answers, err = explicitAnswers(*answersFile, answerValues)
		if err != nil {
			return err
		}
	}
	if *discard && (*resume || *yes || *wait || *to != "") {
		return usageError("--discard cannot be combined with resume, yes, wait or to")
	}
	root := *project
	if root == "" {
		root, err = a.Cwd()
		if err != nil {
			return err
		}
	}
	root, err = filepath.Abs(root)
	if err != nil {
		return err
	}
	if err = checkParents(root); err != nil {
		return err
	}
	stateDir := projectStateDir(cfg.StateDir, root)
	statePath := filepath.Join(stateDir, "submission.json")
	reviewPath := filepath.Join(stateDir, "review.json")
	var state projectSubmission
	if *discard {
		if err = readJSON(statePath, 2<<20, &state); err != nil {
			return err
		}
		if state.SubmissionStarted || state.JobID != "" {
			return errors.New("cannot discard a submitted project; use run --resume")
		}
		state.TerminalState = "abandoned"
		state.PendingAnswers = nil
		if err = session.SavePrivateJSON(statePath, state); err != nil {
			return err
		}
		fmt.Fprintln(a.Stdout, "Discarded local project preview")
		return nil
	}
	if *to != "" {
		if !filepath.IsAbs(*to) {
			*to = filepath.Join(root, *to)
		}
		*to, err = filepath.Abs(*to)
		if err != nil {
			return err
		}
		if _, e := os.Lstat(*to); e == nil {
			return errors.New("fetch destination already exists")
		} else if !os.IsNotExist(e) {
			return e
		}
		if err = checkParents(filepath.Dir(*to)); err != nil {
			return err
		}
		*wait = true
	}
	reader := bufio.NewReader(a.Stdin)
	var review projectReview
	if *resume {
		if err = readJSON(statePath, 2<<20, &state); err != nil {
			return fmt.Errorf("no resumable project submission: %w", err)
		}
		if !validID(state.Key) || !validID(state.PreviewKey) || state.PreviewID != "" && !validID(state.PreviewID) {
			return errors.New("invalid persisted project submission")
		}
		if state.TerminalState != "" {
			return projectPreviewError(state.PreviewID, state.TerminalState, state.TerminalErrorCode, state.TerminalErrorMessage, "")
		}
	} else {
		c, loadErr := loadProject(root)
		if os.IsNotExist(loadErr) {
			return a.projectDiscovery(root)
		}
		if loadErr != nil {
			return loadErr
		}
		data, scanErr := packaging.SelectFiles(root, c.Data, packaging.Rules{MaxFiles: 511, MaxBytes: 1 << 30, Exclude: []string{"results/", "outputs/", "state/"}})
		if scanErr != nil {
			return usageError("invalid declared project data: " + scanErr.Error())
		}
		if err = validateProjectData(c, data); err != nil {
			return err
		}
		rules, err := projectRules(root, cfg.StateDir, *to, c, data)
		if err != nil {
			return err
		}
		code, err := packaging.Scan(root, rules)
		if err != nil {
			return err
		}
		if len(code.Files) == 0 {
			return usageError("project code snapshot is empty")
		}
		configJSON, _ := json.Marshal(c)
		reviewFingerprint := fingerprint(string(configJSON), code.Digest, data.Digest)
		a.projectInventory(c, code, data)
		if err = readJSON(reviewPath, 262144, &review); err != nil && !os.IsNotExist(err) {
			return err
		}
		if *yes {
			if review.SchemaVersion != "1" || !review.Approved || review.Fingerprint != reviewFingerprint {
				return usageError("--yes requires a saved explicit review of this unchanged code, data and configuration; run campus run interactively first")
			}
			if err = selectedCode(root, code, review.SelectedFiles, review.ShareWithLLM); err != nil {
				return err
			}
		} else {
			selected, share, e := a.projectSharing(reader, root, code)
			if e != nil {
				return e
			}
			review = projectReview{SchemaVersion: "1", Fingerprint: reviewFingerprint, SelectedFiles: selected, ShareWithLLM: share}
		}
		cAPI, err := a.member(ctx, cfg)
		if err != nil {
			return err
		}
		account := fingerprint(cAPI.BaseURL, cAPI.Credential)
		if *yes && review.Account != account {
			return usageError("saved project review belongs to a different coordinator/account; run campus run interactively to review this account")
		}
		if err = session.SavePrivateJSON(filepath.Join(stateDir, "scope.json"), map[string]string{"project": root}); err != nil {
			return err
		}
		unlock, err := lockState(filepath.Join(stateDir, "submit"))
		if err != nil {
			return err
		}
		defer unlock()
		var previous projectSubmission
		if e := readJSON(statePath, 2<<20, &previous); e == nil && previous.JobID == "" && previous.TerminalState == "" {
			return errors.New("a project submission is pending; use campus run --resume or --discard")
		} else if e != nil && !os.IsNotExist(e) {
			return e
		}
		temp, err := session.PrivateTempDir(stateDir, "snapshot-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(temp)
		zipPath := filepath.Join(temp, "project.zip")
		z, err := os.OpenFile(zipPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		frozenCode, err := packaging.PackageProject(root, rules, z)
		closeErr := z.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
		if frozenCode.Digest != code.Digest {
			return errors.New("project code changed during review; review again")
		}
		projectArtifact, reused, err := artifacts.UploadOwned(ctx, cAPI, zipPath, "project")
		if err != nil {
			return err
		}
		fmt.Fprintf(a.Stdout, "Project artifact %s (reused=%t)\n", projectArtifact.ArtifactID, reused)
		request := projectPreviewRequest{ProjectConfig: c, ProjectArtifactID: projectArtifact.ArtifactID, Inputs: []generated.InputBinding{}, SelectedFiles: review.SelectedFiles, ShareWithLLM: review.ShareWithLLM, Prompt: c.Prompt}
		if request.Prompt == "" {
			request.Prompt = "Review this declared distributed project. Preserve the exact stage identities, literal commands, dependencies, data selection, outputs, accelerators, resources and time limits. Explain the plan and ask for clarification if needed."
		}
		for _, file := range data.Files {
			destination := filepath.Join(temp, "inputs", filepath.FromSlash(file.Path))
			if err = snapshotData(root, destination, file); err != nil {
				return err
			}
			artifact, reused, e := artifacts.UploadOwned(ctx, cAPI, destination, "input")
			if e != nil {
				return e
			}
			archive := false
			request.Inputs = append(request.Inputs, generated.InputBinding{ArtifactID: artifact.ArtifactID, MountPath: "/inputs/" + file.Path, Archive: &archive})
			fmt.Fprintf(a.Stdout, "Data %s -> %s (reused=%t)\n", file.Path, artifact.ArtifactID, reused)
		}
		state = projectSubmission{Endpoint: cAPI.BaseURL, Account: account, Key: session.UUID(), PreviewKey: session.UUID(), ProjectDigest: projectArtifact.Digest, Fingerprint: reviewFingerprint, Request: request}
		if err = session.SavePrivateJSON(statePath, state); err != nil {
			return err
		}
		return a.continueProject(ctx, cfg, cAPI, state, statePath, reviewPath, review, reader, *yes, *wait, *to, nil)
	}
	cAPI, err := a.member(ctx, cfg)
	if err != nil {
		return err
	}
	if state.Endpoint != cAPI.BaseURL || state.Account != fingerprint(cAPI.BaseURL, cAPI.Credential) {
		return errors.New("persisted project submission belongs to a different coordinator/account; restore its credential to resume")
	}
	unlock, err := lockState(filepath.Join(stateDir, "submit"))
	if err != nil {
		return err
	}
	defer unlock()
	if err = readJSON(statePath, 2<<20, &state); err != nil {
		return err
	}
	if *yes && !state.Approved {
		return usageError("--yes cannot approve a project preview that has not been explicitly reviewed")
	}
	return a.continueProject(ctx, cfg, cAPI, state, statePath, reviewPath, review, reader, *yes, *wait, *to, answers)
}

func (a *App) continueProject(ctx context.Context, cfg config, c *client.Client, state projectSubmission, statePath, reviewPath string, review projectReview, reader *bufio.Reader, yes, wait bool, to string, answers []previewAnswer) error {
	if len(answers) > 0 && (state.JobID != "" || state.SubmissionStarted) {
		return errors.New("answers cannot change a submitted project job or unknown job acknowledgement")
	}
	if state.JobID != "" {
		if !validID(state.JobID) {
			return errors.New("invalid saved project job ID")
		}
		fmt.Fprintln(a.Stdout, state.JobID)
		return a.projectFollow(ctx, cfg, c, state.JobID, wait, to)
	}
	if state.SubmissionStarted {
		if !state.Approved || !validID(state.PreviewID) || !validRevision(state.Revision) {
			return errors.New("persisted project lacks its approved frozen preview/revision")
		}
		return a.projectSubmit(ctx, cfg, c, state, statePath, wait, to)
	}
	var analysis preview
	saveAnswers := func(pending *pendingAnswers, revision json.RawMessage) error {
		state.PendingAnswers = pending
		state.Revision = revision
		return session.SavePrivateJSON(statePath, state)
	}
	if state.PendingAnswers != nil {
		if len(answers) > 0 && (answers[0].QuestionID != state.PendingAnswers.Request.QuestionID || answers[0].Value != state.PendingAnswers.Request.Answer) {
			return errors.New("an answer acknowledgement is unresolved; resume without new answers to inspect the same preview")
		}
		var err error
		analysis, err = reconcilePreviewAnswers(ctx, c, state.PreviewID, state.PendingAnswers, saveAnswers)
		if err != nil {
			return err
		}
		answers = nil
	}
	if state.PreviewID == "" {
		if state.PreviewStarted {
			return errors.New("project preview creation acknowledgement unknown; use run --discard for this never-submitted preview; no automatic replay")
		}
		state.PreviewStarted = true
		if err := session.SavePrivateJSON(statePath, state); err != nil {
			return err
		}
		if err := c.DoOnce(ctx, "POST", "/job-previews", state.Request, &analysis, state.PreviewKey); err != nil {
			return fmt.Errorf("project preview acknowledgement unknown or rejected; use run --discard before a fresh preview: %w", err)
		}
		if !validID(analysis.ID) {
			return errors.New("coordinator returned an invalid project preview ID")
		}
		state.PreviewID = analysis.ID
		if err := session.SavePrivateJSON(statePath, state); err != nil {
			return err
		}
		if analysis.State == "" {
			analysis = preview{}
		}
	}
	for {
		if analysis.ID == "" {
			if err := c.Do(ctx, "GET", "/job-previews/"+state.PreviewID, nil, &analysis, ""); err != nil {
				return err
			}
		}
		if analysis.ID != state.PreviewID {
			return errors.New("coordinator returned a mismatched project preview ID")
		}
		switch analysis.State {
		case "ready":
			if len(answers) > 0 {
				return errors.New("project preview is already ready; resume without answer options to review its plan")
			}
			if err := validateProjectPlan(analysis, state); err != nil {
				return fmt.Errorf("ready project plan changed the reviewed contract; no job submitted: %w", err)
			}
			fmt.Fprintln(a.Stdout, "Plan:", string(analysis.Plan))
			if !yes {
				if err := a.confirmProject(reader); err != nil {
					return err
				}
			}
			review = projectReview{SchemaVersion: "1", Fingerprint: state.Fingerprint, Account: state.Account, ShareWithLLM: state.Request.ShareWithLLM, SelectedFiles: state.Request.SelectedFiles, Approved: true}
			if err := session.SavePrivateJSON(reviewPath, review); err != nil {
				return err
			}
			state.Approved = true
			state.Revision = analysis.Revision
			state.SubmissionStarted = true
			if err := session.SavePrivateJSON(statePath, state); err != nil {
				return err
			}
			return a.projectSubmit(ctx, cfg, c, state, statePath, wait, to)
		case "needs_input":
			if len(answers) > 0 {
				var question struct {
					ID string `json:"question_id"`
				}
				if json.Unmarshal(analysis.Question, &question) != nil || question.ID != answers[0].QuestionID {
					return usageError("answer question_id does not match the project's current preview question")
				}
				request := answerRequest{Revision: analysis.Revision, QuestionID: answers[0].QuestionID, Answer: answers[0].Value}
				if err := validateAnswers(request); err != nil {
					return err
				}
				state.PendingAnswers = &pendingAnswers{Request: request, Key: session.UUID()}
				state.Approved = false
				if err := session.SavePrivateJSON(statePath, state); err != nil {
					return err
				}
				var err error
				analysis, err = sendPreviewAnswers(ctx, c, state.PreviewID, state.PendingAnswers, saveAnswers)
				if err != nil {
					return err
				}
				answers = nil
				continue
			}
			if validRevision(analysis.Revision) {
				state.Revision = analysis.Revision
				if err := session.SavePrivateJSON(statePath, state); err != nil {
					return err
				}
			}
			if err := json.NewEncoder(a.Stdout).Encode(map[string]any{"id": state.PreviewID, "state": analysis.State, "plan_revision": analysis.Revision, "question": analysis.Question}); err != nil {
				return err
			}
			return &statusError{3, errors.New("")}
		case "failed", "cancelled", "expired", "rejected":
			state.TerminalState = analysis.State
			state.TerminalErrorCode = safePreviewDetail(analysis.ErrorCode, c.Credential, 64)
			state.TerminalErrorMessage = safePreviewDetail(analysis.ErrorMessage, c.Credential, 1024)
			if err := session.SavePrivateJSON(statePath, state); err != nil {
				return err
			}
			return projectPreviewError(state.PreviewID, analysis.State, state.TerminalErrorCode, state.TerminalErrorMessage, "")
		case "inspecting", "planning", "validating":
		default:
			return fmt.Errorf("unsupported project preview state %q", analysis.State)
		}
		if err := projectPoll(ctx); err != nil {
			return err
		}
		analysis = preview{}
	}
}

func safePreviewDetail(value, credential string, limit int) string {
	if credential != "" {
		value = strings.ReplaceAll(value, credential, "[redacted]")
	}
	value = strings.Map(func(char rune) rune {
		if unicode.IsControl(char) {
			return ' '
		}
		return char
	}, value)
	value = strings.Join(strings.Fields(value), " ")
	if len(value) > limit {
		value = value[:limit]
		for !utf8.ValidString(value) {
			value = value[:len(value)-1]
		}
		value += "..."
	}
	return value
}

func projectPreviewError(id, state, code, message, credential string) error {
	text := fmt.Sprintf("project preview %s is %s", id, state)
	if code = safePreviewDetail(code, credential, 64); code != "" {
		text += " (" + code + ")"
	}
	if message = safePreviewDetail(message, credential, 1024); message != "" {
		text += ": " + message
	}
	return errors.New(text)
}

func projectPoll(ctx context.Context) error {
	timer := time.NewTimer(500 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (a *App) projectSubmit(ctx context.Context, cfg config, c *client.Client, state projectSubmission, statePath string, wait bool, to string) error {
	var job struct {
		ID string `json:"id"`
	}
	if err := c.DoOnce(ctx, "POST", "/jobs", map[string]any{"preview_id": state.PreviewID, "plan_revision": state.Revision}, &job, state.Key); err != nil {
		return fmt.Errorf("project job acknowledgement unknown or rejected; use campus run --resume with the same saved key: %w", err)
	}
	if !validID(job.ID) {
		return errors.New("invalid project job ID; use campus run --resume")
	}
	state.JobID = job.ID
	if err := session.SavePrivateJSON(statePath, state); err != nil {
		return fmt.Errorf("job %s accepted but receipt could not be saved: %w", job.ID, err)
	}
	fmt.Fprintln(a.Stdout, job.ID)
	return a.projectFollow(ctx, cfg, c, job.ID, wait, to)
}

func (a *App) projectFollow(ctx context.Context, cfg config, c *client.Client, id string, wait bool, to string) error {
	if !wait {
		return nil
	}
	previous := ""
	for {
		var job struct {
			ID       string           `json:"id"`
			State    string           `json:"state"`
			Accepted int64            `json:"accepted_units"`
			Total    int64            `json:"total_units"`
			Tasks    []generated.Task `json:"tasks"`
		}
		if err := c.Do(ctx, "GET", "/jobs/"+id, nil, &job, ""); err != nil {
			return err
		}
		if job.ID != id {
			return errors.New("coordinator returned a mismatched project job ID")
		}
		if job.Accepted < 0 || job.Total < job.Accepted {
			return errors.New("coordinator returned invalid project progress")
		}
		stateJSON, _ := json.Marshal(job)
		progress := fingerprint(string(stateJSON))
		if progress != previous {
			fmt.Fprintf(a.Stdout, "Job %s: %s; accepted %d/%d units\n", id, job.State, job.Accepted, job.Total)
			for _, task := range job.Tasks {
				node := "unassigned"
				if task.NodeID != nil {
					node = *task.NodeID
				}
				fmt.Fprintf(a.Stdout, "  %s/%s: %s (%s)\n", task.StageID, task.ID, task.State, node)
			}
			previous = progress
		}
		switch job.State {
		case "succeeded":
			if to != "" {
				return a.fetch(ctx, cfg, []string{id, "--to", to})
			}
			return nil
		case "failed", "cancelled", "expired", "partial":
			status := fmt.Errorf("project job %s is %s; accepted %d/%d units; inspect campus logs %s and campus fetch %s --to DIR", id, job.State, job.Accepted, job.Total, id, id)
			if job.State == "partial" && to != "" {
				if err := a.fetch(ctx, cfg, []string{id, "--to", to}); err != nil {
					return errors.Join(status, err)
				}
			}
			return status
		case "queued", "running", "verifying", "pending", "waiting_for_capacity":
		default:
			return fmt.Errorf("unsupported project job state %q", job.State)
		}
		if err := projectPoll(ctx); err != nil {
			return err
		}
	}
}
