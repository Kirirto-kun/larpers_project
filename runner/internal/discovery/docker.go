package discovery

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"unicode"
)

// RequireLocalDocker resolves the effective endpoint with the Docker CLI itself.
// This is metadata inspection only: it never contacts the selected daemon. The
// caller must pin the returned endpoint before subsequent Docker operations.
// In particular, CLI versions can differ in DOCKER_HOST/DOCKER_CONTEXT precedence.
func RequireLocalDocker(ctx context.Context, run Command, getenv func(string) string) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	selection := "current Docker context"
	if getenv != nil && (getenv("DOCKER_HOST") != "" || getenv("DOCKER_CONTEXT") != "") {
		selection = "Docker environment selection"
	}
	raw, err := run(ctx, "docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")
	if ctx.Err() != nil {
		return "", ctx.Err()
	}
	if err != nil {
		// Do not echo context output or environment values: an untrusted remote
		// endpoint may contain credentials. Keep the original error private too.
		return "", fmt.Errorf("cannot inspect %s; install/start Docker Desktop and check its context", selection)
	}
	var endpoint string
	if len(raw) > 16<<10 || json.Unmarshal(raw, &endpoint) != nil || !localDockerEndpoint(endpoint) {
		return "", errors.New("participation requires a local Docker unix socket or named pipe; TCP, SSH, remote and unknown endpoints are refused")
	}
	return endpoint, nil
}

func localDockerEndpoint(endpoint string) bool {
	if endpoint == "" || len(endpoint) > 4096 || strings.ContainsFunc(endpoint, unicode.IsControl) || strings.Contains(endpoint, "\\") {
		return false
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" || u.Host != "" || strings.Contains(u.Path, "\\") || strings.ContainsFunc(u.Path, unicode.IsControl) {
		return false
	}
	switch u.Scheme {
	case "unix":
		return strings.HasPrefix(u.Path, "/") && !strings.HasPrefix(u.Path, "//") && len(u.Path) > 1
	case "npipe":
		const prefix = "//./pipe/"
		if !strings.HasPrefix(u.Path, prefix) || len(u.Path) == len(prefix) {
			return false
		}
		for _, part := range strings.Split(strings.TrimPrefix(u.Path, prefix), "/") {
			if part == "" || part == "." || part == ".." {
				return false
			}
		}
		return true
	}
	return false
}
