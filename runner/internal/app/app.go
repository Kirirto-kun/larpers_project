// Package app implements the Campus command line without invoking a host shell.
package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/discovery"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const usage = `Usage: campus [--state-dir DIR] [--endpoint URL] [--ca-file FILE] COMMAND

Commands:
  install --catalog-url URL --catalog-sha256 HEX  Download, cache and connect
  connect --bundle DIR --manifest-sha256 HEX  Prepare and connect this device
  doctor [--profiles FILE] [--gpu]        Inspect host and probe prepared runtimes
  join INVITE [--no-serve] [--gpu]        Redeem a device invitation and participate
  serve [--profiles FILE] [--gpu]         Offer this device until stopped
  leave --drain | --stop                 Finish active work or stop immediately
  login [--email EMAIL] [--password-stdin] Save a private member credential
  logout                                Revoke and remove the saved CLI login
  run [--yes] [--wait] [--to DIR]         Run the campus.json distributed project
  run [--gpu] [--data DIR:/data] [--output PATH] -- PROGRAM [ARG...]
  run --resume                          Reconcile the persisted preview/submission
  run --resume --answers FILE            Answer the saved preview, then run its plan
  run --discard                         Discard a never-submitted local preview
  logs JOB                              Read bounded job logs
  fetch JOB --to DIR                     Download verified artifacts atomically
  cancel JOB                            Cancel a job
  benchmark-local --manifest FILE --repetitions 3 --out FILE
  version                               Print the runner version

Device invitations do not grant member scope. Use campus login or CAMPUS_TOKEN.
Add --managed to install/connect/join/serve to allow web pause/resume and contribution modes.
Use campus COMMAND --help for command options. HTTPS certificate verification is required;
HTTP is supported only for loopback development coordinators.
`

// App's dependencies permit alternate inventory/runtime integrations while the
// default command uses the real host discovery, Docker probes and worker loop.
type App struct {
	Stdout, Stderr      io.Writer
	Stdin               io.Reader
	Getenv              func(string) string
	Cwd                 func() (string, error)
	Discover            func(context.Context) (client.Capabilities, error)
	Probe               func(context.Context, string, string, bool) error
	VerifyImage         func(context.Context, string, string) error
	ResolveImage        func(context.Context, string) (string, error)
	Serve               func(context.Context, *worker.Runner, client.Capabilities, bool) error
	LocalDocker         func(context.Context) (string, error)
	PinDocker           func(string) (func(), error)
	LoadImage           func(context.Context, string) error
	StartDocker         func(context.Context) error
	ManagedPollInterval time.Duration
}

type config struct {
	StateDir, Endpoint, CAFile string
	TrustedCAPEM               []byte
}
type statusError struct {
	code int
	err  error
}

func (e *statusError) Error() string  { return e.err.Error() }
func usageError(message string) error { return &statusError{2, errors.New(message)} }
func (a *App) defaults() {
	if a.ManagedPollInterval <= 0 {
		a.ManagedPollInterval = 5 * time.Second
	}
	if a.Stdin == nil {
		a.Stdin = os.Stdin
	}
	if a.Stdout == nil {
		a.Stdout = os.Stdout
	}
	if a.Stderr == nil {
		a.Stderr = os.Stderr
	}
	if a.Getenv == nil {
		a.Getenv = os.Getenv
	}
	if a.Cwd == nil {
		a.Cwd = os.Getwd
	}
	if a.Discover == nil {
		a.Discover = discovery.Discover
	}
	if a.Probe == nil {
		a.Probe = func(ctx context.Context, image, platform string, gpu bool) error {
			return discovery.Probe(ctx, image, platform, gpu, discovery.ProbeExec)
		}
	}
	if a.VerifyImage == nil {
		a.VerifyImage = func(ctx context.Context, image, platform string) error {
			return (&worker.Runner{}).Docker.VerifyImage(ctx, image, platform)
		}
	}
	if a.ResolveImage == nil {
		a.ResolveImage = func(ctx context.Context, image string) (string, error) {
			return resolveImageReference(ctx, image, discovery.Exec)
		}
	}
	if a.Serve == nil {
		a.Serve = func(ctx context.Context, r *worker.Runner, c client.Capabilities, gpu bool) error {
			return r.Serve(ctx, c, gpu)
		}
	}
	if a.LocalDocker == nil {
		a.LocalDocker = func(ctx context.Context) (string, error) {
			return discovery.RequireLocalDocker(ctx, discovery.Exec, a.Getenv)
		}
	}
	if a.PinDocker == nil {
		a.PinDocker = pinDockerEndpoint
	}
	if a.LoadImage == nil {
		a.LoadImage = loadConnectionImage
	}
	if a.StartDocker == nil {
		a.StartDocker = startConnectionDocker
	}
}

// Execute returns 0 on success, 1 for an operational error, 2 for usage, 3 when a
// preview needs user input, and 130 after context cancellation.
func Execute(ctx context.Context, args []string, stdout, stderr io.Writer) int {
	return (&App{Stdout: stdout, Stderr: stderr}).Run(ctx, args)
}
func (a *App) Run(ctx context.Context, args []string) int {
	a.defaults()
	cfg, rest, err := a.globals(args)
	if err == nil {
		if len(rest) == 0 {
			fmt.Fprint(a.Stdout, usage)
			return 0
		}
		command, args := rest[0], rest[1:]
		switch command {
		case "install":
			err = a.install(ctx, cfg, args)
		case "connect":
			err = a.connect(ctx, cfg, args)
		case "help", "--help", "-h":
			fmt.Fprint(a.Stdout, usage)
			return 0
		case "version", "--version":
			if len(args) > 0 {
				err = usageError("version takes no arguments")
			} else {
				fmt.Fprintln(a.Stdout, client.Version)
				return 0
			}
		case "doctor":
			err = a.doctor(ctx, cfg, args)
		case "join":
			err = a.join(ctx, cfg, args)
		case "serve":
			err = a.serve(ctx, cfg, args)
		case "leave":
			err = a.leave(cfg, args)
		case "login":
			err = a.login(ctx, cfg, args)
		case "logout":
			err = a.logout(ctx, cfg, args)
		case "run":
			err = a.run(ctx, cfg, args)
		case "logs":
			err = a.logs(ctx, cfg, args)
		case "fetch":
			err = a.fetch(ctx, cfg, args)
		case "cancel":
			err = a.cancel(ctx, cfg, args)
		case "benchmark-local":
			err = a.benchmark(ctx, cfg, args)
		default:
			err = usageError("unknown command " + command)
		}
	}
	if err == nil || errors.Is(err, flag.ErrHelp) {
		return 0
	}
	var status *statusError
	if errors.As(err, &status) {
		if status.err.Error() != "" {
			fmt.Fprintln(a.Stderr, "campus:", status.err)
		}
		return status.code
	}
	fmt.Fprintln(a.Stderr, "campus:", err)
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return 130
	}
	return 1
}

func (a *App) globals(args []string) (config, []string, error) {
	cfg := config{StateDir: a.Getenv("CAMPUS_STATE_DIR"), Endpoint: a.Getenv("CAMPUS_ENDPOINT"), CAFile: a.Getenv("CAMPUS_CA_FILE")}
	var rest []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--" {
			rest = append(rest, args[i:]...)
			break
		}
		name, value, hasValue := strings.Cut(arg, "=")
		var target *string
		switch name {
		case "--state-dir":
			target = &cfg.StateDir
		case "--endpoint":
			target = &cfg.Endpoint
		case "--ca-file":
			target = &cfg.CAFile
		}
		if target == nil {
			rest = append(rest, arg)
			continue
		}
		if !hasValue {
			i++
			if i >= len(args) {
				return cfg, nil, usageError(name + " requires a value")
			}
			value = args[i]
		}
		if value == "" {
			return cfg, nil, usageError(name + " requires a nonempty value")
		}
		*target = value
	}
	if cfg.StateDir == "" {
		d, e := session.DefaultDir()
		if e != nil {
			return cfg, nil, e
		}
		cfg.StateDir = d
	}
	var err error
	cfg.StateDir, err = filepath.Abs(cfg.StateDir)
	return cfg, rest, err
}

func (a *App) flags(name string) *flag.FlagSet {
	f := flag.NewFlagSet("campus "+name, flag.ContinueOnError)
	f.SetOutput(a.Stdout)
	return f
}

// parse allows command flags after a positional ID, preserving everything after
// -- as argv. The standard flag package still validates every flag/value.
func parse(f *flag.FlagSet, args []string) ([]string, error) {
	var options, positionals []string
	for i := 0; i < len(args); i++ {
		s := args[i]
		if s == "--" {
			positionals = append(positionals, args[i+1:]...)
			break
		}
		if s == "-h" || s == "--help" {
			f.Usage()
			return nil, flag.ErrHelp
		}
		if !strings.HasPrefix(s, "-") || s == "-" {
			positionals = append(positionals, s)
			continue
		}
		name, _, equal := strings.Cut(strings.TrimLeft(s, "-"), "=")
		option := f.Lookup(name)
		if option == nil {
			return nil, usageError("unknown flag " + s)
		}
		options = append(options, s)
		boolean, ok := option.Value.(interface{ IsBoolFlag() bool })
		if !equal && !(ok && boolean.IsBoolFlag()) {
			i++
			if i >= len(args) {
				return nil, usageError(s + " requires a value")
			}
			options = append(options, args[i])
		}
	}
	if err := f.Parse(options); err != nil {
		return nil, &statusError{2, err}
	}
	return positionals, nil
}

func readJSON(path string, limit int64, out any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return err
	}
	if !st.Mode().IsRegular() || st.Size() > limit {
		return errors.New("JSON file is not regular or exceeds its size limit")
	}
	decoder := json.NewDecoder(io.LimitReader(f, limit+1))
	if err = decoder.Decode(out); err != nil {
		return err
	}
	var extra any
	if err = decoder.Decode(&extra); err != io.EOF {
		return errors.New("unexpected trailing JSON data")
	}
	return nil
}
func loadIdentity(dir string) (session.Identity, error) {
	var identity session.Identity
	err := readJSON(filepath.Join(dir, "identity.json"), 65536, &identity)
	if os.IsNotExist(err) {
		return identity, nil
	}
	return identity, err
}

func apiClient(cfg config, endpoint, credential, sessionID string) (*client.Client, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12}
	if cfg.CAFile != "" || len(cfg.TrustedCAPEM) > 0 {
		data := cfg.TrustedCAPEM
		var err error
		if cfg.CAFile != "" {
			data, err = os.ReadFile(cfg.CAFile)
		}
		if err != nil {
			return nil, fmt.Errorf("read CA file: %w", err)
		}
		if len(data) > 2<<20 {
			return nil, errors.New("CA file exceeds 2 MiB")
		}
		roots, err := x509.SystemCertPool()
		if err != nil {
			roots = x509.NewCertPool()
		}
		if !roots.AppendCertsFromPEM(data) {
			return nil, errors.New("CA file contains no certificates")
		}
		transport.TLSClientConfig.RootCAs = roots
	}
	return client.New(endpoint, credential, sessionID, &http.Client{Transport: transport, Timeout: 40 * time.Second})
}

func (a *App) member(ctx context.Context, cfg config) (*client.Client, error) {
	identity, err := loadIdentity(cfg.StateDir)
	if err != nil {
		return nil, err
	}
	credential := a.Getenv("CAMPUS_TOKEN")
	endpoint := cfg.Endpoint
	if credential == "" {
		var saved memberLogin
		e := readJSON(filepath.Join(cfg.StateDir, "member.json"), 3<<20, &saved)
		if e == nil {
			if endpoint != "" {
				probe, err := apiClient(cfg, endpoint, "", "")
				if err != nil {
					return nil, err
				}
				if probe.BaseURL != saved.Endpoint {
					return nil, errors.New("saved member login belongs to a different coordinator; run campus login for this endpoint or set CAMPUS_TOKEN")
				}
			}
			if saved.Token == "" || saved.UserID == "" || saved.Endpoint == "" {
				return nil, errors.New("invalid saved member login; run campus login again")
			}
			if saved.ExpiresAt != nil {
				expires, err := time.Parse(time.RFC3339Nano, *saved.ExpiresAt)
				if err != nil || !expires.After(time.Now()) {
					return nil, errors.New("saved member login expired; run campus login again")
				}
			}
			credential, endpoint = saved.Token, saved.Endpoint
			if cfg.CAFile == "" {
				cfg.TrustedCAPEM = []byte(saved.CAPEM)
			}
		} else if !os.IsNotExist(e) {
			return nil, e
		}
	} else {
		// CI may override just the credential while retaining the selected
		// coordinator and its private approved CA. Never transfer saved trust to
		// a different explicitly selected endpoint.
		var saved memberLogin
		if e := readJSON(filepath.Join(cfg.StateDir, "member.json"), 3<<20, &saved); e == nil {
			if endpoint == "" {
				endpoint = saved.Endpoint
			}
			check, e := apiClient(config{}, endpoint, "", "")
			if e == nil && check.BaseURL == saved.Endpoint && cfg.CAFile == "" {
				cfg.TrustedCAPEM = []byte(saved.CAPEM)
			}
		}
	}
	if credential == "" {
		credential = identity.MemberCredential
	}
	if credential == "" {
		return nil, errors.New("member credential required: run campus login or set CAMPUS_TOKEN; a device invitation grants participation only")
	}
	if endpoint == "" {
		endpoint = identity.Endpoint
	}
	if endpoint == "" {
		return nil, errors.New("coordinator endpoint required: set CAMPUS_ENDPOINT or --endpoint")
	}
	c, err := apiClient(cfg, endpoint, credential, "")
	if err != nil {
		return nil, err
	}
	if err = c.Handshake(ctx); err != nil {
		return nil, err
	}
	return c, nil
}
