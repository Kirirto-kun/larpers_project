package app

import (
	"bufio"
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/term"
)

type memberLogin struct {
	Endpoint  string  `json:"endpoint"`
	Token     string  `json:"token"`
	UserID    string  `json:"user_id"`
	PoolID    string  `json:"pool_id"`
	Name      string  `json:"name"`
	Role      string  `json:"role"`
	ExpiresAt *string `json:"expires_at"`
	CAPEM     string  `json:"ca_pem,omitempty"`
}

func fingerprint(parts ...string) string {
	sum := sha256.Sum256([]byte(strings.Join(parts, "\x00")))
	return hex.EncodeToString(sum[:])
}

func readLine(reader *bufio.Reader, limit int) (string, error) {
	var result []byte
	for {
		part, prefix, err := reader.ReadLine()
		if len(result)+len(part) > limit {
			return "", errors.New("input exceeds its size limit")
		}
		result = append(result, part...)
		if err != nil {
			return "", err
		}
		if !prefix {
			return string(result), nil
		}
	}
}

func (a *App) login(ctx context.Context, cfg config, args []string) error {
	f := a.flags("login")
	email := f.String("email", "", "account email (prompted when omitted)")
	passwordStdin := f.Bool("password-stdin", false, "read the password from stdin; never put passwords in argv")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 {
		return usageError("login accepts --email and --password-stdin; passwords must not be passed in argv")
	}
	root, e := a.Cwd()
	if e != nil {
		return e
	}
	for projectRoot := root; ; projectRoot = filepath.Dir(projectRoot) {
		_, campusErr := os.Lstat(filepath.Join(projectRoot, "campus.json"))
		_, gitErr := os.Lstat(filepath.Join(projectRoot, ".git"))
		if campusErr == nil || gitErr == nil {
			rel, e := filepath.Rel(projectRoot, cfg.StateDir)
			if e == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
				return usageError("member credentials must be stored outside the project; choose --state-dir outside the project directory")
			}
			break
		}
		if filepath.Dir(projectRoot) == projectRoot {
			break
		}
	}
	inputFile, isFile := a.Stdin.(*os.File)
	if !*passwordStdin && (!isFile || !term.IsTerminal(int(inputFile.Fd()))) {
		return usageError("login requires an interactive terminal for a hidden password, or --email EMAIL --password-stdin")
	}
	reader := bufio.NewReader(a.Stdin)
	if *email == "" {
		if *passwordStdin {
			return usageError("--password-stdin requires --email EMAIL")
		}
		fmt.Fprint(a.Stdout, "Email: ")
		*email, err = readLine(reader, 320)
		if err != nil {
			return usageError("could not read email")
		}
	}
	*email = strings.TrimSpace(*email)
	if !strings.Contains(*email, "@") || len(*email) > 320 || strings.ContainsAny(*email, "\r\n\x00") {
		return usageError("a valid account email is required")
	}
	endpoint := cfg.Endpoint
	if endpoint == "" {
		identity, e := loadIdentity(cfg.StateDir)
		if e != nil {
			return e
		}
		endpoint = identity.Endpoint
	}
	if cfg.CAFile != "" {
		trusted, e := os.ReadFile(cfg.CAFile)
		if e != nil {
			return errors.New("could not read approved CA file")
		}
		if len(trusted) > 2<<20 {
			return usageError("CA file exceeds 2 MiB")
		}
		cfg.TrustedCAPEM = trusted
		cfg.CAFile = ""
	}
	if endpoint == "" {
		return usageError("login requires --endpoint URL or CAMPUS_ENDPOINT")
	}
	c, err := apiClient(cfg, endpoint, "", "")
	if err != nil {
		return err
	}
	var password []byte
	if *passwordStdin {
		value, e := readLine(reader, 4096)
		if e != nil && !errors.Is(e, io.EOF) {
			return usageError("could not read password from stdin")
		}
		password = []byte(value)
	} else {
		fmt.Fprint(a.Stdout, "Password: ")
		password, err = term.ReadPassword(int(inputFile.Fd()))
		fmt.Fprintln(a.Stdout)
		if err != nil {
			return errors.New("could not read hidden password")
		}
	}
	defer clear(password)
	if len(password) == 0 || len(password) > 4096 || strings.ContainsRune(string(password), 0) {
		return usageError("password must contain 1–4096 bytes")
	}
	if err = c.Handshake(ctx); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return errors.New("could not verify coordinator protocol for login")
	}
	var saved memberLogin
	if err = c.DoOnce(ctx, "POST", "/auth/cli-login", map[string]string{"email": *email, "password": string(password)}, &saved, ""); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		// Authentication responses are untrusted and can echo submitted secrets.
		var api *client.APIError
		if errors.As(err, &api) {
			return fmt.Errorf("login rejected (HTTP %d)", api.Status)
		}
		return errors.New("login acknowledgement unavailable; retry campus login")
	}
	if saved.Token == "" || len(saved.Token) > 4096 || strings.ContainsAny(saved.Token, "\x00\r\n") || !validID(saved.UserID) || !validID(saved.PoolID) {
		return errors.New("coordinator returned an invalid CLI login")
	}
	if saved.ExpiresAt != nil {
		expires, e := time.Parse(time.RFC3339Nano, *saved.ExpiresAt)
		if e != nil || !expires.After(time.Now()) {
			return errors.New("coordinator returned an expired CLI login")
		}
	}
	saved.Endpoint = c.BaseURL
	saved.CAPEM = string(cfg.TrustedCAPEM)
	// Named copies preserve endpoint/account separation; member.json selects the
	// most recent login without modifying the worker's identity.json.
	path := filepath.Join(cfg.StateDir, "members", fingerprint(saved.Endpoint, saved.UserID)+".json")
	if err = session.SavePrivateJSON(path, saved); err != nil {
		return err
	}
	if err = session.SavePrivateJSON(filepath.Join(cfg.StateDir, "member.json"), saved); err != nil {
		return err
	}
	fmt.Fprintln(a.Stdout, "Signed in to", c.BaseURL)
	return nil
}

func (a *App) logout(ctx context.Context, cfg config, args []string) error {
	f := a.flags("logout")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 {
		return usageError("logout takes no arguments")
	}
	var saved memberLogin
	path := filepath.Join(cfg.StateDir, "member.json")
	if err = readJSON(path, 3<<20, &saved); err != nil {
		if os.IsNotExist(err) {
			return errors.New("no saved CLI login")
		}
		return err
	}
	if cfg.CAFile == "" {
		cfg.TrustedCAPEM = []byte(saved.CAPEM)
	}
	if cfg.Endpoint != "" {
		check, e := apiClient(cfg, cfg.Endpoint, "", "")
		if e != nil {
			return e
		}
		if check.BaseURL != saved.Endpoint {
			return errors.New("saved CLI login belongs to a different coordinator")
		}
	}
	c, err := apiClient(cfg, saved.Endpoint, saved.Token, "")
	if err != nil {
		return err
	}
	if err = c.DoOnce(ctx, "POST", "/auth/cli-logout", nil, nil, ""); err != nil {
		var api *client.APIError
		if !errors.As(err, &api) || api.Status != 401 {
			return errors.New("CLI logout could not be confirmed; saved login retained")
		}
	}
	if err = os.Remove(path); err != nil {
		return err
	}
	if err = os.Remove(filepath.Join(cfg.StateDir, "members", fingerprint(saved.Endpoint, saved.UserID)+".json")); err != nil && !os.IsNotExist(err) {
		return err
	}
	fmt.Fprintln(a.Stdout, "Signed out")
	return nil
}
