package app

import (
	"campus/runner/internal/artifacts"
	"campus/runner/internal/client"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type stringList []string

func (v *stringList) String() string     { return strings.Join(*v, ", ") }
func (v *stringList) Set(s string) error { *v = append(*v, s); return nil }

// These request DTOs are provisional until foundation publishes generated API v1
// contracts. The CLI and Python SDK use the same preview -> jobs boundary.
type runRequest struct {
	Argv          []string  `json:"argv"`
	RequiresGPU   bool      `json:"requires_gpu"`
	WorkingDir    string    `json:"working_dir"`
	Outputs       []string  `json:"outputs"`
	Bindings      []binding `json:"bindings"`
	NetworkPolicy string    `json:"network_policy"`
}
type binding struct {
	ArtifactID string `json:"artifact_id"`
	Target     string `json:"target"`
	ReadOnly   bool   `json:"read_only"`
}
type projectInput struct {
	Artifact       client.Artifact           `json:"artifact"`
	ManifestDigest string                    `json:"manifest_digest"`
	Manifest       packaging.ProjectManifest `json:"manifest"`
}
type previewRequest struct {
	Request runRequest        `json:"request"`
	Project projectInput      `json:"project"`
	Inputs  []client.Artifact `json:"inputs"`
}
type preview struct {
	ID        string          `json:"preview_id"`
	State     string          `json:"state"`
	Revision  json.RawMessage `json:"plan_revision"`
	Questions json.RawMessage `json:"questions"`
	Plan      json.RawMessage `json:"plan"`
}
type submission struct {
	Endpoint          string          `json:"endpoint,omitempty"`
	PreviewID         string          `json:"preview_id"`
	Key               string          `json:"idempotency_key"`
	PreviewKey        string          `json:"preview_key,omitempty"`
	Revision          json.RawMessage `json:"plan_revision,omitempty"`
	JobID             string          `json:"job_id,omitempty"`
	SubmissionStarted bool            `json:"submission_started,omitempty"`
	PreviewTerminal   bool            `json:"preview_terminal,omitempty"`
	TerminalState     string          `json:"terminal_state,omitempty"`
	PendingAnswers    *pendingAnswers `json:"pending_answers,omitempty"`
	Request           *previewRequest `json:"preview_request,omitempty"`
}

func validID(id string) bool { return client.IDPattern.MatchString(id) && !strings.Contains(id, "..") }

func parseData(value string) (string, error) {
	// Split on the last colon so C:\datasets\train:/data remains a Windows path.
	index := strings.LastIndex(value, ":")
	if index <= 0 || value[index+1:] != "/data" {
		return "", usageError("--data must be a local directory followed by :/data (one binding is supported)")
	}
	root, err := filepath.Abs(value[:index])
	if err != nil {
		return "", err
	}
	st, err := os.Lstat(root)
	if err != nil {
		return "", err
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("--data source must be a regular directory, not a symlink")
	}
	return root, nil
}

func snapshot(ctx context.Context, c *client.Client, root, destination, kind string, rules packaging.Rules) (client.Artifact, packaging.ProjectManifest, error) {
	var artifact client.Artifact
	f, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return artifact, packaging.ProjectManifest{}, err
	}
	m, err := packaging.PackageProject(root, rules, f)
	closeErr := f.Close()
	if err != nil {
		return artifact, m, err
	}
	if closeErr != nil {
		return artifact, m, closeErr
	}
	artifact, err = artifacts.Upload(ctx, c, destination, client.Fence{})
	artifact.Kind = kind
	artifact.Path = kind + ".zip"
	if kind == "dataset" {
		artifact.Path = "data.zip"
	}
	return artifact, m, err
}

func (a *App) run(ctx context.Context, cfg config, args []string) error {
	f := a.flags("run")
	gpu := f.Bool("gpu", false, "require GPU; never silently fall back to CPU")
	data := f.String("data", "", "explicit dataset directory mounted read-only at /data")
	project := f.String("project", "", "selected project root (default current directory)")
	resume := f.Bool("resume", false, "reconcile the persisted preview and submission key")
	discard := f.Bool("discard", false, "discard a never-submitted local preview; preserves unknown job acknowledgements")
	answersFile := f.String("answers", "", "explicit JSON answers for the saved preview (requires --resume)")
	var answerValues stringList
	f.Var(&answerValues, "answer", "question_id=value string answer (repeatable; requires --resume)")
	var outputs stringList
	f.Var(&outputs, "output", "declared relative output path (repeatable)")
	separator := -1
	for i, s := range args {
		if s == "--" {
			separator = i
			break
		}
	}
	options := args
	var argv []string
	if separator >= 0 {
		options = args[:separator]
		argv = append([]string(nil), args[separator+1:]...)
	}
	rest, err := parse(f, options)
	if err != nil {
		return err
	}
	if len(rest) != 0 {
		return usageError("run accepts command argv only after --")
	}
	if *discard {
		if separator >= 0 || *gpu || *data != "" || *project != "" || len(outputs) > 0 || *answersFile != "" || len(answerValues) > 0 {
			return usageError("run --discard takes no command, data, output or answer options")
		}
		return a.discardPreview(cfg)
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
	if *resume {
		if separator >= 0 || *gpu || *data != "" || *project != "" || len(outputs) > 0 {
			return usageError("run --resume takes no new command, data or output options")
		}
	} else {
		if separator < 0 || len(argv) == 0 {
			return usageError("run requires -- followed by a program and its arguments")
		}
		if len(argv) > 1024 {
			return usageError("command has too many arguments")
		}
		var total int
		for _, arg := range argv {
			total += len(arg)
			if strings.ContainsRune(arg, 0) {
				return usageError("NUL in command argument")
			}
		}
		if total > 65536 {
			return usageError("command arguments exceed 64 KiB")
		}
		for _, output := range outputs {
			if err = packaging.SafePath(output); err != nil {
				return usageError(err.Error())
			}
		}
	}
	var dataRoot string
	if *data != "" {
		dataRoot, err = parseData(*data)
		if err != nil {
			return err
		}
	}
	c, err := a.member(ctx, cfg)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(cfg.StateDir, 0700); err != nil {
		return err
	}
	unlock, err := lockState(filepath.Join(cfg.StateDir, "submit"))
	if err != nil {
		return err
	}
	defer unlock()
	statePath := filepath.Join(cfg.StateDir, "submission.json")
	var state submission
	if *resume {
		if err = readJSON(statePath, 8<<20, &state); err != nil {
			return fmt.Errorf("no resumable submission: %w", err)
		}
		if !validID(state.Key) || (state.PreviewID != "" && !validID(state.PreviewID)) {
			return errors.New("invalid persisted submission identity")
		}
		if state.Endpoint != "" && state.Endpoint != c.BaseURL {
			return errors.New("pending submission belongs to a different coordinator")
		}
		if state.JobID != "" {
			if len(answers) > 0 {
				return errors.New("answers cannot change an already submitted job")
			}
			if !validID(state.JobID) {
				return errors.New("invalid persisted job ID")
			}
			fmt.Fprintln(a.Stdout, state.JobID)
			return nil
		}
		if state.SubmissionStarted {
			if len(answers) > 0 {
				return errors.New("answers cannot change a job whose acknowledgement is unknown; run --resume without answer options")
			}
			if !validID(state.PreviewID) || !validRevision(state.Revision) {
				return errors.New("persisted submission lacks its frozen preview/revision")
			}
			return a.submit(ctx, c, state, statePath)
		}
		if state.PreviewTerminal && terminalPreviewState(state.TerminalState) {
			return fmt.Errorf("preview %s is %s; start a new run", state.PreviewID, state.TerminalState)
		}
	} else {
		var previous submission
		if e := readJSON(statePath, 8<<20, &previous); e == nil && previous.JobID == "" && (previous.SubmissionStarted || !previous.PreviewTerminal || !terminalPreviewState(previous.TerminalState)) {
			return errors.New("a submission is pending; run campus run --resume to reconcile it before starting another")
		} else if e != nil && !os.IsNotExist(e) {
			return e
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
		st, err := os.Lstat(root)
		if err != nil {
			return err
		}
		if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
			return errors.New("project root must be a regular directory")
		}
		temp, err := os.MkdirTemp(cfg.StateDir, "snapshot-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(temp)
		// Exclude the runner state and an explicit in-project dataset from the
		// project ZIP. The latter is uploaded once as a separate dataset artifact.
		var ignored []string
		for _, exclude := range []string{cfg.StateDir, dataRoot} {
			if exclude == "" {
				continue
			}
			rel, e := filepath.Rel(root, exclude)
			if e == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
				ignored = append(ignored, filepath.ToSlash(rel)+"/")
			}
			if e == nil && rel == "." && exclude == cfg.StateDir {
				return errors.New("project root must not be the runner state directory")
			}
		}
		artifact, manifest, err := snapshot(ctx, c, root, filepath.Join(temp, "project.zip"), "project", packaging.Rules{MaxFiles: 10000, MaxBytes: 256 << 20, Ignore: ignored})
		if err != nil {
			return err
		}
		request := &previewRequest{Request: runRequest{Argv: argv, RequiresGPU: *gpu, WorkingDir: "/workspace", Outputs: outputs, Bindings: []binding{}, NetworkPolicy: "none"}, Project: projectInput{Artifact: artifact, ManifestDigest: manifest.Digest, Manifest: manifest}, Inputs: []client.Artifact{}}
		if request.Request.Outputs == nil {
			request.Request.Outputs = []string{}
		}
		if dataRoot != "" {
			artifact, _, err = snapshot(ctx, c, dataRoot, filepath.Join(temp, "data.zip"), "dataset", packaging.Rules{MaxFiles: 10000, MaxBytes: 1 << 30})
			if err != nil {
				return err
			}
			request.Inputs = append(request.Inputs, artifact)
			request.Request.Bindings = append(request.Request.Bindings, binding{ArtifactID: artifact.ArtifactID, Target: "/data", ReadOnly: true})
		}
		state = submission{Endpoint: c.BaseURL, Key: session.UUID(), PreviewKey: session.UUID(), Request: request}
		if err = session.AtomicJSON(statePath, state); err != nil {
			return err
		}
	}
	var analysis preview
	if state.PendingAnswers != nil {
		if len(answers) > 0 {
			supplied, _ := json.Marshal(answers)
			saved, _ := json.Marshal(state.PendingAnswers.Request.Answers)
			if string(supplied) != string(saved) {
				return errors.New("an answer acknowledgement is unresolved; run --resume without new answers to replay the saved batch")
			}
		}
		analysis, err = a.sendAnswers(ctx, c, &state, statePath)
		if err != nil {
			return err
		}
		answers = nil
	}
	if state.PreviewID == "" {
		if state.Request == nil || !validID(state.PreviewKey) {
			return errors.New("persisted submission has no preview request")
		}
		if err = c.Do(ctx, "POST", "/job-previews", state.Request, &analysis, state.PreviewKey); err != nil {
			return fmt.Errorf("preview response unknown; retry campus run --resume with the saved request/key: %w", err)
		}
		if !validID(analysis.ID) {
			return errors.New("coordinator returned an invalid preview_id")
		}
		state.PreviewID = analysis.ID
		if err = session.AtomicJSON(statePath, state); err != nil {
			return err
		}
		if analysis.State == "" {
			analysis = preview{}
		}
	}
	for {
		if analysis.ID == "" {
			if err = c.Do(ctx, "GET", "/job-previews/"+state.PreviewID, nil, &analysis, ""); err != nil {
				return err
			}
		}
		if analysis.ID != state.PreviewID {
			return errors.New("coordinator returned a mismatched preview_id")
		}
		switch analysis.State {
		case "needs_input":
			if len(answers) > 0 {
				request := answerRequest{Revision: analysis.Revision, Answers: answers}
				if err = validateAnswers(request); err != nil {
					return err
				}
				state.PendingAnswers = &pendingAnswers{Request: request, Key: session.UUID()}
				if err = session.AtomicJSON(statePath, state); err != nil {
					return err
				}
				analysis, err = a.sendAnswers(ctx, c, &state, statePath)
				if err != nil {
					return err
				}
				answers = nil
				continue
			}
			if validRevision(analysis.Revision) {
				state.Revision = analysis.Revision
				if err = session.AtomicJSON(statePath, state); err != nil {
					return err
				}
			}
			if err = json.NewEncoder(a.Stdout).Encode(map[string]any{"preview_id": state.PreviewID, "state": "needs_input", "questions": analysis.Questions}); err != nil {
				return err
			}
			return &statusError{3, errors.New("")}
		case "ready":
			if len(answers) > 0 {
				return errors.New("preview is already ready; no answers were sent and no job was submitted; resume without answer options to accept its plan")
			}
			if !validRevision(analysis.Revision) {
				return errors.New("ready preview has invalid plan_revision")
			}
			// Freeze the revision before POST. A lost acknowledgement is retried
			// with precisely the same preview, revision and idempotency key.
			state.Revision = analysis.Revision
			state.SubmissionStarted = true
			if err = session.AtomicJSON(statePath, state); err != nil {
				return err
			}
			if len(analysis.Plan) > 0 && string(analysis.Plan) != "null" {
				fmt.Fprintln(a.Stdout, "Plan:", string(analysis.Plan))
			}
			return a.submit(ctx, c, state, statePath)
		case "failed", "cancelled", "expired", "rejected":
			state.PreviewTerminal = true
			state.TerminalState = analysis.State
			if err = session.AtomicJSON(statePath, state); err != nil {
				return err
			}
			return fmt.Errorf("preview %s is %s", state.PreviewID, analysis.State)
		case "pending", "queued", "analyzing", "planning", "preparing":
		default:
			return fmt.Errorf("unsupported preview state %q", analysis.State)
		}
		timer := time.NewTimer(250 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
		analysis = preview{}
	}
}

func validRevision(raw json.RawMessage) bool {
	if len(raw) == 0 || string(raw) == "null" {
		return false
	}
	var integer int64
	if json.Unmarshal(raw, &integer) == nil {
		return integer >= 0
	}
	var value string
	return json.Unmarshal(raw, &value) == nil && value != "" && len(value) <= 128 && !strings.ContainsAny(value, "\x00\r\n")
}

func (a *App) submit(ctx context.Context, c *client.Client, state submission, path string) error {
	var job struct {
		JobID string `json:"job_id"`
	}
	if err := c.Do(ctx, "POST", "/jobs", map[string]any{"preview_id": state.PreviewID, "plan_revision": state.Revision}, &job, state.Key); err != nil {
		return fmt.Errorf("job acknowledgement unknown or rejected; use campus run --resume (same saved idempotency key): %w", err)
	}
	if !validID(job.JobID) {
		return errors.New("coordinator returned an invalid job_id; use campus run --resume to reconcile")
	}
	state.JobID = job.JobID
	if err := session.AtomicJSON(path, state); err != nil {
		return fmt.Errorf("job %s accepted but saving its receipt failed: %w", job.JobID, err)
	}
	fmt.Fprintln(a.Stdout, job.JobID)
	return nil
}

func (a *App) logs(ctx context.Context, cfg config, args []string) error {
	f := a.flags("logs")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 1 || !validID(rest[0]) {
		return usageError("logs requires one valid JOB ID")
	}
	c, err := a.member(ctx, cfg)
	if err != nil {
		return err
	}
	cursor := ""
	seen := map[string]bool{}
	var total int
	for page := 0; page < 1000; page++ {
		route := "/jobs/" + rest[0] + "/logs"
		if cursor != "" {
			route += "?cursor=" + strings.ReplaceAll(url.QueryEscape(cursor), ".", "%2E")
		}
		var response struct {
			Records    []json.RawMessage `json:"records"`
			NextCursor *string           `json:"next_cursor"`
		}
		if err = c.Do(ctx, "GET", route, nil, &response, ""); err != nil {
			return err
		}
		for _, record := range response.Records {
			var text string
			if json.Unmarshal(record, &text) != nil {
				var line struct {
					Text    *string `json:"text"`
					Message *string `json:"message"`
				}
				if err = json.Unmarshal(record, &line); err != nil {
					return err
				}
				if line.Text == nil {
					line.Text = line.Message
				}
				if line.Text == nil {
					return errors.New("log record has no text")
				}
				text = *line.Text
			}
			total += len(text) + 1
			if total > 4<<20 {
				return errors.New("log output exceeds 4 MiB; use a narrower server-side log export")
			}
			if _, err = fmt.Fprintln(a.Stdout, strings.TrimSuffix(text, "\n")); err != nil {
				return err
			}
		}
		if response.NextCursor == nil || *response.NextCursor == "" {
			return nil
		}
		cursor = *response.NextCursor
		if len(cursor) > 4096 || seen[cursor] {
			return errors.New("invalid or repeated log cursor")
		}
		seen[cursor] = true
	}
	return errors.New("log page limit reached")
}

func (a *App) cancel(ctx context.Context, cfg config, args []string) error {
	f := a.flags("cancel")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 1 || !validID(rest[0]) {
		return usageError("cancel requires one valid JOB ID")
	}
	c, err := a.member(ctx, cfg)
	if err != nil {
		return err
	}
	if err = c.Do(ctx, "POST", "/jobs/"+rest[0]+"/cancel", map[string]string{}, nil, "cancel:"+rest[0]); err != nil {
		return err
	}
	fmt.Fprintln(a.Stdout, rest[0], "cancellation requested")
	return nil
}

func (a *App) fetch(ctx context.Context, cfg config, args []string) error {
	f := a.flags("fetch")
	to := f.String("to", "", "nonexisting destination directory")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 1 || !validID(rest[0]) || *to == "" {
		return usageError("fetch requires JOB and --to DIR (destination must not exist)")
	}
	destination, err := filepath.Abs(*to)
	if err != nil {
		return err
	}
	if _, err = os.Lstat(destination); err == nil {
		return errors.New("fetch destination already exists")
	} else if !os.IsNotExist(err) {
		return err
	}
	if err = checkParents(filepath.Dir(destination)); err != nil {
		return err
	}
	c, err := a.member(ctx, cfg)
	if err != nil {
		return err
	}
	var job struct {
		JobID     string            `json:"job_id"`
		State     string            `json:"state"`
		Artifacts []client.Artifact `json:"artifacts"`
	}
	if err = c.Do(ctx, "GET", "/jobs/"+rest[0], nil, &job, ""); err != nil {
		return err
	}
	if job.JobID != rest[0] {
		return errors.New("coordinator returned a mismatched job_id")
	}
	if len(job.Artifacts) == 0 {
		return fmt.Errorf("job %s has no downloadable artifacts (state %s)", job.JobID, job.State)
	}
	if err = validateDownloads(job.Artifacts); err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	temp, err := os.MkdirTemp(filepath.Dir(destination), ".campus-fetch-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temp)
	for _, artifact := range job.Artifacts {
		if err = artifacts.Download(ctx, c, artifact, filepath.Join(temp, filepath.FromSlash(artifact.Path))); err != nil {
			return err
		}
	}
	if err = checkParents(filepath.Dir(destination)); err != nil {
		return err
	}
	if _, err = os.Lstat(destination); err == nil {
		return errors.New("fetch destination appeared during download")
	} else if !os.IsNotExist(err) {
		return err
	}
	if err = os.Rename(temp, destination); err != nil {
		return err
	}
	fmt.Fprintln(a.Stdout, destination)
	return nil
}

func validateDownloads(files []client.Artifact) error {
	if len(files) > 10000 {
		return errors.New("artifact file count exceeds 10000")
	}
	names := map[string]bool{}
	var total int64
	for _, a := range files {
		if !validID(a.ArtifactID) || !client.DigestPattern.MatchString(a.Digest) || a.SizeBytes < 0 || a.SizeBytes > (1<<30)-total {
			return errors.New("invalid artifact descriptor or 1 GiB download quota exceeded")
		}
		total += a.SizeBytes
		if err := packaging.SafePath(a.Path); err != nil {
			return err
		}
		key := strings.ToLower(a.Path)
		if names[key] {
			return errors.New("case-colliding artifact paths")
		}
		names[key] = true
	}
	pathFiles, dirs := map[string]bool{}, map[string]bool{}
	for name := range names {
		for _, candidate := range []string{name, name + ".part"} {
			if pathFiles[candidate] || dirs[candidate] {
				return errors.New("artifact path collides with another file/directory or download staging path")
			}
			pathFiles[candidate] = true
			for parent := filepath.ToSlash(filepath.Dir(filepath.FromSlash(candidate))); parent != "."; parent = filepath.ToSlash(filepath.Dir(filepath.FromSlash(parent))) {
				if pathFiles[parent] {
					return errors.New("artifact file/directory staging path collision")
				}
				dirs[parent] = true
			}
		}
	}
	return nil
}

func checkParents(path string) error {
	for {
		st, err := os.Lstat(path)
		if err == nil {
			if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
				return errors.New("destination parent must be a directory without symlinks")
			}
		} else if !os.IsNotExist(err) {
			return err
		}
		parent := filepath.Dir(path)
		if parent == path {
			return nil
		}
		path = parent
	}
}
