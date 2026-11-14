package app

import (
	"bufio"
	"campus/runner/internal/client"
	"campus/runner/internal/onboarding"
	"campus/runner/internal/telemetry"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

func pinDockerEndpoint(endpoint string) (func(), error) {
	if !strings.HasPrefix(endpoint, "unix:///") && !strings.HasPrefix(endpoint, "npipe:////./pipe/") {
		return nil, errors.New("a local Docker socket is required")
	}
	oldHost, hostSet := os.LookupEnv("DOCKER_HOST")
	oldContext, contextSet := os.LookupEnv("DOCKER_CONTEXT")
	restore := func() {
		if hostSet {
			os.Setenv("DOCKER_HOST", oldHost)
		} else {
			os.Unsetenv("DOCKER_HOST")
		}
		if contextSet {
			os.Setenv("DOCKER_CONTEXT", oldContext)
		} else {
			os.Unsetenv("DOCKER_CONTEXT")
		}
	}
	if err := os.Setenv("DOCKER_HOST", endpoint); err != nil {
		return nil, err
	}
	if err := os.Unsetenv("DOCKER_CONTEXT"); err != nil {
		restore()
		return nil, err
	}
	return restore, nil
}

func loadConnectionImage(ctx context.Context, path string) error {
	bounded, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	command := exec.CommandContext(bounded, "docker", "image", "load", "--input", path)
	diagnostics := telemetry.NewLogBuffer(8192, nil)
	command.Stdout = diagnostics
	command.Stderr = diagnostics
	if err := command.Run(); err != nil {
		return fmt.Errorf("не удалось загрузить подготовленное окружение: %w (%s)", err, diagnostics.Bytes())
	}
	return nil
}
func startConnectionDocker(ctx context.Context) error {
	bounded, cancel := context.WithTimeout(ctx, 90*time.Second)
	defer cancel()
	command := exec.CommandContext(bounded, "docker", "desktop", "start")
	diagnostics := telemetry.NewLogBuffer(4096, nil)
	command.Stdout = diagnostics
	command.Stderr = diagnostics
	if err := command.Run(); err != nil {
		return errors.New("запустите Docker Desktop и повторите подключение; если его ещё нет, установите Docker Desktop для вашей ОС")
	}
	return nil
}

func connectionLine(ctx context.Context, reader *bufio.Reader) (string, error) {
	type answer struct {
		value string
		err   error
	}
	done := make(chan answer, 1)
	go func() {
		raw, err := reader.ReadSlice('\n')
		if err == io.EOF && len(raw) > 0 {
			err = nil
		}
		if err == nil && len(raw) > 8192 {
			err = errors.New("input too long")
		}
		if err == nil && !strings.ContainsRune(string(raw), 0) {
			done <- answer{strings.TrimSpace(string(raw)), nil}
		} else {
			done <- answer{"", errors.New("не удалось прочитать ответ; приглашение или согласие не были приняты")}
		}
	}()
	select {
	case <-ctx.Done():
		return "", ctx.Err()
	case value := <-done:
		return value.value, value.err
	}
}
func sameCoordinator(a, b string) bool {
	left, e1 := client.New(a, "", "", nil)
	right, e2 := client.New(b, "", "", nil)
	return e1 == nil && e2 == nil && left.BaseURL == right.BaseURL
}

func (a *App) connect(ctx context.Context, cfg config, args []string) error {
	flags := a.flags("connect")
	root := flags.String("bundle", "", "prepared connection kit directory")
	digest := flags.String("manifest-sha256", "", "trusted connection manifest SHA256")
	check := flags.Bool("check-only", false, "verify this device without enrolling")
	gpu := flags.Bool("gpu", false, "explicitly enable verified NVIDIA computation")
	stop := flags.Bool("stop", false, "stop local participation immediately")
	drain := flags.Bool("drain", false, "finish active work, then stop")
	rest, err := parse(flags, args)
	if err != nil {
		return err
	}
	if len(rest) > 0 || (*stop && *drain) {
		return usageError("connect accepts one bundle and one optional stop/drain action")
	}
	if *stop || *drain {
		action := "--stop"
		if *drain {
			action = "--drain"
		}
		return a.leave(cfg, []string{action})
	}
	if *root == "" || *digest == "" {
		return usageError("connect requires --bundle DIR --manifest-sha256 TRUSTED_SHA256")
	}
	bundle, err := onboarding.Load(ctx, *root, *digest, runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return err
	}
	metadataRoot, cleanup, err := bundle.SnapshotMetadata(ctx)
	if err != nil {
		return err
	}
	defer cleanup()
	metadataPath := func(file onboarding.File) string { return filepath.Join(metadataRoot, filepath.FromSlash(file.Path)) }
	if bundle.Manifest.Endpoint != "" {
		if cfg.Endpoint != "" && !sameCoordinator(cfg.Endpoint, bundle.Manifest.Endpoint) {
			return errors.New("настройка адреса относится к другому серверу; используйте адрес этого комплекта")
		}
		cfg.Endpoint = bundle.Manifest.Endpoint
	}
	if bundle.Manifest.CAFile != nil {
		cfg.CAFile = metadataPath(*bundle.Manifest.CAFile)
	}
	prepared, _, err := readProfiles(metadataPath(bundle.Manifest.Profiles))
	if err != nil {
		return err
	}
	if len(prepared.Profiles) != len(bundle.Manifest.Images) {
		return errors.New("комплект должен описывать все и только свои подготовленные профили")
	}
	for _, image := range bundle.Manifest.Images {
		matched := false
		for _, p := range prepared.Profiles {
			if p.ImageDigest != nil && *p.ImageDigest == image.ImageDigest && p.Accelerator == image.Accelerator && p.Platform == image.Platform {
				matched = true
			}
		}
		if !matched {
			return errors.New("образ комплекта не соответствует утверждённому профилю")
		}
	}
	endpoint, err := a.LocalDocker(ctx)
	if err != nil {
		return fmt.Errorf("нужен локальный Docker Desktop: %w", err)
	}
	restore, err := a.PinDocker(endpoint)
	if err != nil {
		return err
	}
	defer restore()
	caps, err := a.Discover(ctx)
	if err != nil {
		return err
	}
	if !caps.Runtime.Healthy {
		fmt.Fprintln(a.Stdout, "Запускаю Docker Desktop…")
		if err = a.StartDocker(ctx); err != nil {
			return err
		}
		ready, cancel := context.WithTimeout(ctx, 120*time.Second)
		defer cancel()
		for !caps.Runtime.Healthy {
			select {
			case <-ready.Done():
				return errors.New("Docker ещё не готов; завершите его первоначальную настройку и повторите запуск")
			case <-time.After(time.Second):
			}
			caps, err = a.Discover(ready)
			if err != nil {
				return err
			}
		}
	}
	model := caps.CPUModel
	if model == "" {
		model = caps.OS + "/" + caps.Arch
	}
	fmt.Fprintf(a.Stdout, "Устройство: %s, логических CPU: %d\n", model, caps.CPULogical)
	if caps.MemoryTotalBytes > 0 {
		fmt.Fprintf(a.Stdout, "RAM: %.1f GiB, доступно %.1f GiB\n", float64(caps.MemoryTotalBytes)/(1<<30), float64(caps.MemoryAvailableBytes)/(1<<30))
	} else {
		fmt.Fprintf(a.Stdout, "Доступно RAM: %.1f GiB\n", float64(caps.MemoryAvailableBytes)/(1<<30))
	}
	reader := bufio.NewReaderSize(a.Stdin, 8193)
	explicitGPU := false
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "gpu" {
			explicitGPU = true
		}
	})
	if len(caps.GPUs) > 0 {
		for _, device := range caps.GPUs {
			name := device.Name
			if name == "" {
				name = "NVIDIA " + device.ID
			}
			fmt.Fprintf(a.Stdout, "GPU: %s, VRAM %.1f GiB, драйвер %s\n", name, float64(device.VRAMBytes)/(1<<30), device.Driver)
		}
		if !explicitGPU && !*check {
			fmt.Fprint(a.Stdout, "Разрешить задачи на NVIDIA GPU? [y/N]: ")
			answer, e := connectionLine(ctx, reader)
			if e != nil {
				return e
			}
			switch strings.ToLower(answer) {
			case "y", "yes", "д", "да":
				*gpu = true
			case "", "n", "no", "н", "нет":
				*gpu = false
			default:
				return errors.New("выберите y или n; GPU не был подключён")
			}
		}
	} else {
		if len(caps.NVIDIADeviceNames) > 0 {
			fmt.Fprintf(a.Stdout, "NVIDIA обнаружена: %s. CUDA пока недоступна; проверьте драйвер NVIDIA.\n", strings.Join(caps.NVIDIADeviceNames, ", "))
		} else {
			fmt.Fprintln(a.Stdout, "CUDA/NVIDIA для вычислений пока не обнаружена; доступен режим CPU.")
		}
	}
	if *gpu && len(caps.GPUs) == 0 {
		return errors.New("для режима GPU нужен установленный драйвер NVIDIA и успешная CUDA-проверка; CPU можно подключить с --gpu=false")
	}
	if err = bundle.VerifySelected(ctx, *gpu); err != nil {
		return err
	}
	for _, image := range bundle.Manifest.Images {
		if image.Accelerator == "cuda" && !*gpu {
			continue
		}
		if err = a.VerifyImage(ctx, image.ImageDigest, image.Platform); err != nil {
			fmt.Fprintf(a.Stdout, "Подготавливаю окружение %s для первого запуска…\n", strings.ToUpper(image.Accelerator))
			if err = a.LoadImage(ctx, bundle.Path(image.File)); err != nil {
				return err
			}
			if err = a.VerifyImage(ctx, image.ImageDigest, image.Platform); err != nil {
				return err
			}
		}
	}
	caps, _, err = a.preflightWithCanonical(ctx, metadataPath(bundle.Manifest.Profiles), *gpu, !*check)
	if err != nil {
		return err
	}
	fmt.Fprintf(a.Stdout, "Проверка пройдена. Бюджет: до %.2f CPU и %.2f GiB RAM; GPU: %t.\n", float64(caps.OfferedBudget.CPUMillis)/1000, float64(caps.OfferedBudget.MemoryBytes)/(1<<30), *gpu)
	if *check {
		fmt.Fprintln(a.Stdout, "Устройство проверено; приглашение не использовалось.")
		return nil
	}
	identity, err := loadIdentity(cfg.StateDir)
	if err != nil {
		return err
	}
	options := []string{"--profiles", metadataPath(bundle.Manifest.Profiles)}
	if *gpu {
		options = append(options, "--gpu")
	}
	if identity.Credential != "" {
		if cfg.Endpoint != "" && !sameCoordinator(cfg.Endpoint, identity.Endpoint) {
			return errors.New("это устройство уже подключено к другому серверу")
		}
		cfg.Endpoint = identity.Endpoint
		fmt.Fprintln(a.Stdout, "Восстанавливаю сохранённое подключение…")
		return a.serve(ctx, cfg, options)
	}
	var invitation string
	if bundle.Manifest.InviteFile != nil {
		raw, e := os.ReadFile(metadataPath(*bundle.Manifest.InviteFile))
		if e != nil {
			return e
		}
		invitation = strings.TrimSpace(string(raw))
	} else {
		fmt.Fprint(a.Stdout, "Вставьте приглашение от сервера: ")
		invitation, err = connectionLine(ctx, reader)
		if err != nil {
			return err
		}
	}
	if invitation == "" || len(invitation) > 4096 || strings.ContainsAny(invitation, "\x00\r\n") {
		return errors.New("нужно одно действующее приглашение")
	}
	if strings.Contains(invitation, "://") && cfg.Endpoint != "" {
		link, e := url.Parse(invitation)
		if e != nil || !sameCoordinator(link.Scheme+"://"+link.Host, cfg.Endpoint) {
			return errors.New("приглашение относится к другому серверу")
		}
	}
	fmt.Fprintln(a.Stdout, "Подключаю устройство. Оставьте это окно открытым; Ctrl+C останавливает участие.")
	return a.join(ctx, cfg, append(options, "--", invitation))
}
