package app

import (
	"campus/runner/internal/client"
	"campus/runner/internal/discovery"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

type profile struct {
	ID           string  `json:"id"`
	Accelerator  string  `json:"accelerator"`
	Platform     string  `json:"platform"`
	ImageDigest  *string `json:"image_digest"`
	GuardVersion string  `json:"guard_version"`
}
type profiles struct {
	SchemaVersion string    `json:"schema_version"`
	Profiles      []profile `json:"profiles"`
}

func (a *App) profilesFile() string {
	if p := a.Getenv("CAMPUS_PROFILES"); p != "" {
		return p
	}
	if cwd, err := a.Cwd(); err == nil {
		p := filepath.Join(cwd, "environments", "profiles.json")
		if _, err = os.Stat(p); err == nil {
			return p
		}
	}
	if exe, err := os.Executable(); err == nil {
		p := filepath.Join(filepath.Dir(exe), "profiles.json")
		if _, err = os.Stat(p); err == nil {
			return p
		}
	}
	return filepath.Join("environments", "profiles.json")
}

func readProfiles(path string) (profiles, map[string]string, error) {
	var p profiles
	allowed := map[string]string{}
	if err := readJSON(path, 2<<20, &p); err != nil {
		return p, allowed, fmt.Errorf("read approved profiles %s (prepare local pinned images and use --profiles): %w", path, err)
	}
	if p.SchemaVersion != "1" || len(p.Profiles) == 0 || len(p.Profiles) > 128 {
		return p, allowed, errors.New("unsupported or empty approved profiles file")
	}
	ids := map[string]bool{}
	for _, v := range p.Profiles {
		if !client.IDPattern.MatchString(v.ID) || ids[v.ID] || (v.Accelerator != "cpu" && v.Accelerator != "cuda") || (v.Platform != "linux/amd64" && v.Platform != "linux/arm64") || v.GuardVersion != "1" {
			return p, allowed, errors.New("profile requires unique id, supported platform/accelerator and guard_version=1")
		}
		ids[v.ID] = true
		if v.ImageDigest == nil {
			continue
		}
		if !client.ImagePattern.MatchString(*v.ImageDigest) {
			return p, allowed, fmt.Errorf("profile %s requires immutable image_digest (name@sha256:... or local sha256:...)", v.ID)
		}
		if prior, ok := allowed[*v.ImageDigest]; ok && prior != v.Platform {
			return p, allowed, errors.New("image digest has conflicting profile platforms")
		}
		allowed[*v.ImageDigest] = v.Platform
	}
	return p, allowed, nil
}

func (a *App) preflight(ctx context.Context, path string, gpu bool) (client.Capabilities, map[string]string, error) {
	return a.preflightWithCanonical(ctx, path, gpu, false)
}

func (a *App) preflightWithCanonical(ctx context.Context, path string, gpu, requireCanonical bool) (client.Capabilities, map[string]string, error) {
	caps, err := a.Discover(ctx)
	if err != nil {
		return caps, nil, fmt.Errorf("discover host: %w", err)
	}
	for i := range caps.GPUs {
		caps.GPUs[i].CUDAVerified = false
	}
	caps.ImageDigests = []string{}
	caps.OfferedBudget = discovery.Budget(caps, false)
	p, _, err := readProfiles(path)
	allowed := map[string]string{}
	if err != nil {
		return caps, allowed, err
	}
	platform := "linux/" + caps.Arch
	selected := map[string]profile{}
	for _, v := range p.Profiles {
		if v.Platform == platform {
			if _, ok := selected[v.Accelerator]; !ok {
				selected[v.Accelerator] = v
			}
		}
	}
	kinds := []string{"cpu"}
	if gpu {
		kinds = append(kinds, "cuda")
	}
	for _, kind := range kinds {
		v, ok := selected[kind]
		if !ok {
			return caps, allowed, fmt.Errorf("prepare an approved %s profile for %s; emulation is not enabled", kind, platform)
		}
		if v.ImageDigest == nil {
			return caps, allowed, fmt.Errorf("profile %s is unprepared: build/cache its image and set image_digest to its verified SHA-256 digest in %s", v.ID, path)
		}
	}
	if !caps.Runtime.Healthy {
		return caps, allowed, errors.New("Docker Linux runtime unavailable; start Docker Engine/Desktop and check WSL2 on Windows; see inventory diagnostics")
	}
	if gpu && len(caps.GPUs) == 0 {
		return caps, allowed, errors.New("GPU opt in requested but no NVIDIA device was detected; check driver/CUDA runtime")
	}
	for _, kind := range kinds {
		v := selected[kind]
		if err = a.VerifyImage(ctx, *v.ImageDigest, v.Platform); err != nil {
			return caps, allowed, fmt.Errorf("profile %s: %w", v.ID, err)
		}
		// Offline archives can preserve the immutable local image ID without a
		// registry RepoDigest. Verify and exercise that approved identity first;
		// canonical admission metadata must not make local work unavailable.
		if err = a.Probe(ctx, *v.ImageDigest, v.Platform, kind == "cuda"); err != nil {
			return caps, allowed, fmt.Errorf("profile %s: %w", v.ID, err)
		}
		allowed[*v.ImageDigest] = v.Platform
		canonical, resolveErr := a.ResolveImage(ctx, *v.ImageDigest)
		if ctx.Err() != nil {
			return caps, allowed, ctx.Err()
		}
		if resolveErr == nil && (len(canonical) > 256 || !repositoryDigest.MatchString(canonical)) {
			resolveErr = errors.New("cached canonical RepoDigest is unavailable")
		}
		if resolveErr == nil && canonical != *v.ImageDigest {
			resolveErr = a.VerifyImage(ctx, canonical, v.Platform)
			if ctx.Err() != nil {
				return caps, allowed, ctx.Err()
			}
		}
		if resolveErr != nil {
			if requireCanonical {
				return caps, allowed, fmt.Errorf("profile %s: participation requires a verified canonical RepoDigest; offline doctor and benchmark-local can use the approved local image: %w", v.ID, resolveErr)
			}
			caps.Diagnostics = append(caps.Diagnostics, fmt.Sprintf("Profile %s is verified for offline use; participation canonical RepoDigest is not ready: %v", v.ID, resolveErr))
		} else {
			allowed[canonical] = v.Platform
			caps.ImageDigests = append(caps.ImageDigests, canonical)
		}
		if kind == "cuda" {
			// The current real probe addresses CUDA's default device. Never
			// advertise additional GPUs whose computation was not exercised.
			caps.GPUs[0].CUDAVerified = true
			if len(caps.GPUs) > 1 {
				caps.Diagnostics = append(caps.Diagnostics, "CUDA default device was verified; additional detected GPUs are not offered until individually probed.")
			}
		}
	}
	caps.OfferedBudget = discovery.Budget(caps, gpu)
	if caps.OfferedBudget.CPUMillis <= 0 || caps.OfferedBudget.MemoryBytes < 32<<20 {
		return caps, allowed, errors.New("participation paused: AC power and at least 512 MiB available RAM/disk are required")
	}
	return caps, allowed, nil
}

func (a *App) doctor(ctx context.Context, cfg config, args []string) error {
	f := a.flags("doctor")
	file := f.String("profiles", a.profilesFile(), "local administrator-approved profiles JSON")
	gpu := f.Bool("gpu", false, "explicitly verify and offer CUDA")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 {
		return usageError("doctor takes no positional arguments")
	}
	caps, _, checkErr := a.preflight(ctx, *file, *gpu)
	if checkErr != nil {
		caps.Diagnostics = append(caps.Diagnostics, checkErr.Error())
	}
	endpoint := cfg.Endpoint
	if endpoint == "" {
		identity, e := loadIdentity(cfg.StateDir)
		if e != nil {
			return e
		}
		endpoint = identity.Endpoint
	}
	if endpoint != "" {
		c, e := apiClient(cfg, endpoint, "", "")
		if e == nil {
			e = c.Handshake(ctx)
		}
		if e != nil {
			caps.Diagnostics = append(caps.Diagnostics, "Coordinator network/protocol: "+e.Error())
			if checkErr == nil {
				checkErr = e
			}
		}
	}
	if err = json.NewEncoder(a.Stdout).Encode(caps); err != nil {
		return err
	}
	return checkErr
}
