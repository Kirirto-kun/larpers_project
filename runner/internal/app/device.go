package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/containers"
	"campus/runner/internal/session"
	"campus/runner/internal/worker"
	"context"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
)

func invite(value, endpoint string) (string, string, error) {
	token := value
	if strings.Contains(value, "://") {
		u, err := url.Parse(value)
		if err != nil || u.Host == "" || u.User != nil {
			return "", "", usageError("invalid invitation URL")
		}
		if _, err = client.New(u.Scheme+"://"+u.Host, "", "", nil); err != nil {
			return "", "", err
		}
		token = u.Query().Get("token")
		if token == "" && u.Fragment != "" {
			fragment, err := url.ParseQuery(u.Fragment)
			if err != nil {
				return "", "", usageError("invalid invitation fragment")
			}
			token = fragment.Get("token")
			if token == "" && !strings.Contains(u.Fragment, "=") {
				token = u.Fragment
			}
		}
		if token == "" {
			parts := strings.Split(strings.Trim(u.Path, "/"), "/")
			if len(parts) == 2 && (parts[0] == "join" || parts[0] == "invite" || parts[0] == "invitations") {
				token = parts[1]
			}
		}
		if endpoint == "" {
			endpoint = u.Scheme + "://" + u.Host
		}
	}
	if token == "" || len(token) > 4096 || strings.ContainsAny(token, "\x00\r\n") {
		return "", "", usageError("invitation token is missing or invalid")
	}
	if endpoint == "" {
		return "", "", usageError("a token needs --endpoint URL or CAMPUS_ENDPOINT")
	}
	return token, endpoint, nil
}

func lockState(dir string) (func(), error) {
	unlock, err := session.Lock(dir)
	if err != nil {
		return nil, fmt.Errorf("another campus process is using this state directory (or it cannot be locked): %w", err)
	}
	return unlock, nil
}

func (a *App) join(ctx context.Context, cfg config, args []string) error {
	f := a.flags("join")
	file := f.String("profiles", a.profilesFile(), "approved prepared profiles JSON")
	gpu := f.Bool("gpu", false, "opt in to verified CUDA workloads")
	noServe := f.Bool("no-serve", false, "enroll the device without offering resources")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 1 {
		return usageError("join requires one invitation URL or token")
	}
	token, endpoint, err := invite(rest[0], cfg.Endpoint)
	if err != nil {
		return err
	}
	unlock, err := lockState(cfg.StateDir)
	if err != nil {
		return err
	}
	defer unlock()
	identity, err := session.LoadOrCreate(cfg.StateDir)
	if err != nil {
		return err
	}
	if identity.Credential != "" {
		return errors.New("this installation is already joined; use campus serve with its saved identity")
	}
	c, err := apiClient(cfg, endpoint, "", "")
	if err != nil {
		return err
	}
	if err = c.Handshake(ctx); err != nil {
		return err
	}
	var response struct {
		ID     string `json:"id"`
		NodeID string `json:"node_id"`
		Token  string `json:"token"`
		Scope  string `json:"scope"`
	}
	if err = c.DoOnce(ctx, "POST", "/invitations/redeem", map[string]any{"token": token, "installation_id": identity.InstallationID, "protocol_version": 1}, &response, "redeem:"+identity.InstallationID); err != nil {
		return fmt.Errorf("device redemption acknowledgement unknown or rejected; the invitation was not automatically repeated: %w", err)
	}
	if !validID(response.NodeID) || !validID(response.ID) || response.Token == "" || response.Scope != "device" {
		return errors.New("coordinator returned an incomplete device identity")
	}
	if _, err = apiClient(cfg, endpoint, response.Token, ""); err != nil {
		return err
	}
	identity.Endpoint = endpoint
	identity.NodeID = response.NodeID
	identity.CredentialID = response.ID
	identity.Credential = response.Token
	identity.SessionID = ""
	if err = session.Save(cfg.StateDir, identity); err != nil {
		return err
	}
	fmt.Fprintf(a.Stdout, "Joined as node %s.\n", identity.NodeID)
	if *noServe {
		return nil
	}
	return a.serveLocked(ctx, cfg, identity, *file, *gpu)
}

func (a *App) serve(ctx context.Context, cfg config, args []string) error {
	f := a.flags("serve")
	file := f.String("profiles", a.profilesFile(), "approved prepared profiles JSON")
	gpu := f.Bool("gpu", false, "opt in to verified CUDA workloads")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 {
		return usageError("serve takes no positional arguments")
	}
	unlock, err := lockState(cfg.StateDir)
	if err != nil {
		return err
	}
	defer unlock()
	identity, err := loadIdentity(cfg.StateDir)
	if err != nil {
		return err
	}
	if identity.Credential == "" || identity.NodeID == "" || identity.InstallationID == "" {
		return errors.New("this device has not joined; run campus join INVITE first")
	}
	return a.serveLocked(ctx, cfg, identity, *file, *gpu)
}

func (a *App) serveLocked(ctx context.Context, cfg config, identity session.Identity, profilesPath string, gpu bool) error {
	if cfg.Endpoint != "" && strings.TrimSuffix(cfg.Endpoint, "/") != strings.TrimSuffix(identity.Endpoint, "/") {
		return errors.New("saved device identity belongs to a different endpoint; use its saved endpoint for serve")
	}
	c, err := apiClient(cfg, identity.Endpoint, identity.Credential, "")
	if err != nil {
		return err
	}
	if err = c.Handshake(ctx); err != nil {
		return err
	}
	caps, allowed, err := a.preflightWithCanonical(ctx, profilesPath, gpu, true)
	if err != nil {
		return err
	}
	contractCaps, err := caps.Contract()
	if err != nil {
		return fmt.Errorf("publish preflight capabilities: %w", err)
	}
	var response struct {
		SessionID string `json:"id"`
		NodeID    string `json:"node_id"`
	}
	if err = c.DoOnce(ctx, "POST", "/node-sessions", map[string]any{"protocol_version": 1, "capabilities": contractCaps}, &response, "node-session:"+session.UUID()); err != nil {
		return err
	}
	if !validID(response.SessionID) || response.NodeID != identity.NodeID {
		return errors.New("coordinator returned an invalid node session identity")
	}
	c.SessionID = response.SessionID
	identity.SessionID = response.SessionID
	if err = session.Save(cfg.StateDir, identity); err != nil {
		return err
	}
	if err = c.Do(ctx, "PUT", "/node-sessions/"+response.SessionID+"/capabilities", contractCaps, nil, ""); err != nil {
		return err
	}
	r := &worker.Runner{Client: c, Docker: containers.Docker{InstallationID: identity.InstallationID}, StateDir: cfg.StateDir, AllowedImages: allowed, Identity: identity}
	fmt.Fprintln(a.Stdout, "Serving; campus leave --drain finishes active work, --stop stops it.")
	return a.Serve(ctx, r, caps, gpu)
}

func (a *App) leave(cfg config, args []string) error {
	f := a.flags("leave")
	drain := f.Bool("drain", false, "finish active tasks, then stop")
	stop := f.Bool("stop", false, "stop active tasks immediately")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 || *drain == *stop {
		return usageError("leave requires exactly one of --drain or --stop")
	}
	if err = os.MkdirAll(cfg.StateDir, 0700); err != nil {
		return err
	}
	action := "drain"
	if *stop {
		action = "stop"
	}
	if err = session.Control(cfg.StateDir, action); err != nil {
		return err
	}
	fmt.Fprintln(a.Stdout, "Local participation control:", action)
	return nil
}
