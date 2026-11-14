package app

import (
	"campus/runner/internal/artifacts"
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"campus/runner/internal/packaging"
	"campus/runner/internal/session"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type stringList []string

func (v *stringList) String() string     { return strings.Join(*v, ", ") }
func (v *stringList) Set(s string) error { *v = append(*v, s); return nil }

// runRequest records the user's exact command intent locally. The planner only
// receives the published PreviewRequest, with this intent in its bounded prompt.
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
	MountPath  string `json:"mount_path"`
	Archive    bool   `json:"archive"`
}
type previewRequest = generated.PreviewRequest
type preview struct {
	ID       string          `json:"id"`
	State    string          `json:"state"`
	Revision json.RawMessage `json:"plan_revision"`
	Question json.RawMessage `json:"question"`
	Plan     json.RawMessage `json:"plan"`
}
type submission struct {
	Endpoint          string          `json:"endpoint,omitempty"`
	PreviewID         string          `json:"preview_id"`
	Key               string          `json:"idempotency_key"`
	PreviewKey        string          `json:"preview_key,omitempty"`
	PreviewStarted    bool            `json:"preview_started,omitempty"`
	Revision          json.RawMessage `json:"plan_revision,omitempty"`
	JobID             string          `json:"job_id,omitempty"`
	SubmissionStarted bool            `json:"submission_started,omitempty"`
	PreviewTerminal   bool            `json:"preview_terminal,omitempty"`
	TerminalState     string          `json:"terminal_state,omitempty"`
	PendingAnswers    *pendingAnswers `json:"pending_answers,omitempty"`
	Request           *previewRequest `json:"preview_request,omitempty"`
	Intent            *runRequest     `json:"command_intent,omitempty"`
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
	uploadKind := "input"
	if kind == "project" {
		uploadKind = "project"
	}
	artifact, err = artifacts.UploadAs(ctx, c, destination, client.Fence{}, uploadKind)
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
		if len(argv) > 64 {
			return usageError("command has too many arguments")
		}
		var total int
		for _, arg := range argv {
			total += len(arg)
			if len([]rune(arg)) > 4096 {
				return usageError("command argument exceeds 4096 characters")
			}
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
		if len(outputs) > 32 {
			return usageError("at most 32 declared outputs are supported")
		}
		if _, err = commandPrompt(runRequest{Argv: argv, RequiresGPU: *gpu, WorkingDir: "/workspace", Outputs: outputs, NetworkPolicy: "none"}); err != nil {
			return err
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
		artifact, _, err := snapshot(ctx, c, root, filepath.Join(temp, "project.zip"), "project", packaging.Rules{MaxFiles: 10000, MaxBytes: 256 << 20, Ignore: ignored})
		if err != nil {
			return err
		}
		accelerator, share, archive := "cpu", false, true
		if *gpu {
			accelerator = "cuda"
		}
		projectID := artifact.ArtifactID
		request := &previewRequest{ProjectArtifactID: &projectID, Accelerator: &accelerator, ShareWithLlm: &share, SelectedFiles: []string{}, Inputs: []generated.InputBinding{}}
		intent := &runRequest{Argv: argv, RequiresGPU: *gpu, WorkingDir: "/workspace", Outputs: outputs, Bindings: []binding{{ArtifactID: projectID, MountPath: "/workspace", Archive: true}}, NetworkPolicy: "none"}
		if intent.Outputs == nil {
			intent.Outputs = []string{}
		}
		if dataRoot != "" {
			artifact, _, err = snapshot(ctx, c, dataRoot, filepath.Join(temp, "data.zip"), "dataset", packaging.Rules{MaxFiles: 10000, MaxBytes: 1 << 30})
			if err != nil {
				return err
			}
			request.Inputs = append(request.Inputs, generated.InputBinding{ArtifactID: artifact.ArtifactID, MountPath: "/data", Archive: &archive})
			intent.Bindings = append(intent.Bindings, binding{ArtifactID: artifact.ArtifactID, MountPath: "/data", Archive: true})
		}
		prompt, err := commandPrompt(*intent)
		if err != nil {
			return err
		}
		request.Prompt = &prompt
		state = submission{Endpoint: c.BaseURL, Key: session.UUID(), PreviewKey: session.UUID(), Request: request, Intent: intent}
		if err = session.AtomicJSON(statePath, state); err != nil {
			return err
		}
	}
	var analysis preview
	if state.PendingAnswers != nil {
		if len(answers) > 0 {
			supplied, _ := json.Marshal(answers)
			saved, _ := json.Marshal([]previewAnswer{{QuestionID: state.PendingAnswers.Request.QuestionID, Value: state.PendingAnswers.Request.Answer}})
			if string(supplied) != string(saved) {
				return errors.New("an answer acknowledgement is unresolved; run --resume without new answers to inspect the saved preview")
			}
		}
		analysis, err = a.reconcileAnswers(ctx, c, &state, statePath)
		if err != nil {
			return err
		}
		answers = nil
	}
	if state.PreviewID == "" {
		if state.PreviewStarted {
			return errors.New("preview creation acknowledgement is unknown; the API does not promise idempotent replay; use run --discard for this never-submitted preview before starting a new run")
		}
		if state.Request == nil || state.Request.Prompt == nil || *state.Request.Prompt == "" || !validID(state.PreviewKey) {
			return errors.New("persisted submission has no preview request")
		}
		state.PreviewStarted = true
		if err = session.AtomicJSON(statePath, state); err != nil {
			return err
		}
		if err = c.DoOnce(ctx, "POST", "/job-previews", state.Request, &analysis, state.PreviewKey); err != nil {
			return fmt.Errorf("preview creation acknowledgement unknown or rejected; no automatic replay is supported; use run --discard before starting another never-submitted preview: %w", err)
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
				var question struct {
					ID string `json:"question_id"`
				}
				if json.Unmarshal(analysis.Question, &question) != nil || question.ID != answers[0].QuestionID {
					return errors.New("answer question_id does not match the current preview question")
				}
				request := answerRequest{Revision: analysis.Revision, QuestionID: answers[0].QuestionID, Answer: answers[0].Value}
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
			if err = json.NewEncoder(a.Stdout).Encode(map[string]any{"id": state.PreviewID, "state": "needs_input", "plan_revision": analysis.Revision, "question": analysis.Question}); err != nil {
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
			if state.Intent != nil {
				if err = validateCommandPlan(analysis, *state.Intent); err != nil {
					return fmt.Errorf("ready plan changed the requested command; no job submitted: %w; discard this preview or correct the planner", err)
				}
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
		case "inspecting", "planning", "validating":
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
	return json.Unmarshal(raw, &integer) == nil && integer > 0
}

func (a *App) submit(ctx context.Context, c *client.Client, state submission, path string) error {
	var job struct {
		JobID string `json:"id"`
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
	var cursor int64
	var total int
	for page := 0; page < 1000; page++ {
		route := "/jobs/" + rest[0] + "/logs"
		if cursor != 0 {
			route += "?cursor=" + strconv.FormatInt(cursor, 10)
		}
		var response struct {
			Entries *[]struct {
				Cursor int64   `json:"cursor"`
				Text   *string `json:"text"`
				Gap    bool    `json:"gap"`
			} `json:"entries"`
			Cursor    *int64 `json:"cursor"`
			Truncated *bool  `json:"truncated"`
		}
		if err = c.Do(ctx, "GET", route, nil, &response, ""); err != nil {
			return err
		}
		if response.Entries == nil || response.Cursor == nil || response.Truncated == nil {
			return errors.New("malformed log page: entries, cursor and truncated are required")
		}
		if *response.Cursor < cursor {
			return errors.New("log cursor moved backwards")
		}
		if *response.Truncated {
			fmt.Fprintln(a.Stderr, "campus: coordinator reports truncated job logs")
		}
		previous := cursor
		for _, record := range *response.Entries {
			if record.Cursor <= previous || record.Cursor > *response.Cursor || record.Text == nil {
				return errors.New("invalid or repeated log entry cursor")
			}
			previous = record.Cursor
			if record.Gap {
				fmt.Fprintln(a.Stderr, "campus: coordinator reports a gap in job logs")
			}
			text := *record.Text
			total += len(text) + 1
			if total > 4<<20 {
				return errors.New("log output exceeds 4 MiB; use a narrower server-side log export")
			}
			if _, err = fmt.Fprintln(a.Stdout, strings.TrimSuffix(text, "\n")); err != nil {
				return err
			}
		}
		if len(*response.Entries) == 0 {
			return nil
		}
		if *response.Cursor <= cursor {
			return errors.New("invalid or repeated log cursor")
		}
		cursor = *response.Cursor
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
	var job struct {
		ID string `json:"id"`
	}
	if err = c.Do(ctx, "POST", "/jobs/"+rest[0]+"/cancel", nil, &job, "cancel:"+rest[0]); err != nil {
		return err
	}
	if job.ID != rest[0] {
		return errors.New("coordinator returned a mismatched cancelled job id")
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
		JobID     string            `json:"id"`
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
