package app

import (
	"campus/runner/internal/benchmark"
	"campus/runner/internal/session"
	"context"
	"fmt"
	"os"
	"path/filepath"
)

func (a *App) benchmark(ctx context.Context, cfg config, args []string) error {
	f := a.flags("benchmark-local")
	manifest := f.String("manifest", "", "prepared local baseline manifest JSON")
	repetitions := f.Int("repetitions", 3, "number of real sequential container repetitions (1..100)")
	out := f.String("out", "", "StandaloneRecord JSON destination; written only after successful execution")
	profiles := f.String("profiles", a.profilesFile(), "approved prepared profiles JSON")
	gpu := f.Bool("gpu", false, "explicitly permit a GPU assignment in the local manifest")
	rest, err := parse(f, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 || *manifest == "" || *out == "" || *repetitions < 1 || *repetitions > 100 {
		return usageError("benchmark-local requires --manifest FILE --repetitions 1..100 --out FILE")
	}
	_, allowed, err := readProfiles(*profiles)
	if err != nil {
		return err
	}
	if len(allowed) == 0 {
		return fmt.Errorf("approved profiles are unprepared; prepare cached pinned images before benchmark-local")
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
	record, err := benchmark.Run(ctx, benchmark.Options{ManifestPath: *manifest, Repetitions: *repetitions, StateDir: cfg.StateDir, AllowedImages: allowed, Identity: identity, GPUOptIn: *gpu})
	if err != nil {
		return err
	}
	destination, err := filepath.Abs(*out)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(destination), 0700); err != nil {
		return err
	}
	if err = session.AtomicJSON(destination, record); err != nil {
		return err
	}
	fmt.Fprintln(a.Stdout, destination)
	return nil
}
