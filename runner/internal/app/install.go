package app

import (
	"bufio"
	"campus/runner/internal/installer"
	"context"
	"errors"
	"flag"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
)

// install prepares a public, invitation-free release and uses the existing
// verified foreground connection path for preflight, enrollment and reconnect.
func (a *App) install(ctx context.Context, cfg config, args []string) error {
	flags := a.flags("install")
	catalogURL := flags.String("catalog-url", "", "trusted public catalog URL")
	catalogSHA := flags.String("catalog-sha256", "", "trusted public catalog SHA256")
	cache := flags.String("cache-dir", "", "private persistent download cache")
	gpu := flags.Bool("gpu", false, "explicitly permit NVIDIA computation and CUDA download")
	check := flags.Bool("check-only", false, "prepare and check without enrolling")
	stop := flags.Bool("stop", false, "stop local participation immediately")
	drain := flags.Bool("drain", false, "finish active work and stop")
	managed := flags.Bool("managed", false, "allow web pause/resume and bounded contribution profiles")
	rest, err := parse(flags, args)
	if err != nil {
		return err
	}
	if len(rest) != 0 || *stop && *drain {
		return usageError("install accepts catalog options and one optional stop/drain action")
	}
	if *stop || *drain {
		action := "--stop"
		if *drain {
			action = "--drain"
		}
		return a.leave(cfg, []string{action})
	}
	if *catalogURL == "" || *catalogSHA == "" {
		return usageError("install requires --catalog-url URL --catalog-sha256 TRUSTED_SHA256")
	}
	u, err := url.Parse(*catalogURL)
	if err != nil || u.Host == "" || u.User != nil {
		return errors.New("invalid public catalog URL")
	}
	public, err := apiClient(cfg, u.Scheme+"://"+u.Host, "", "")
	if err != nil {
		return err
	}
	public.Managed = *managed
	catalog, err := installer.FetchCatalog(ctx, public.HTTP, *catalogURL, *catalogSHA)
	if err != nil {
		return err
	}
	target, err := catalog.Select(runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return err
	}
	if cfg.Endpoint != "" && !sameCoordinator(cfg.Endpoint, catalog.Endpoint) {
		return errors.New("настройка адреса относится к другому серверу")
	}
	cfg.Endpoint = catalog.Endpoint
	endpoint, err := a.LocalDocker(ctx)
	if err != nil {
		return fmt.Errorf("нужен локальный Docker: %w", err)
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
	caps, err = a.readyConnectionRuntime(ctx, caps)
	if err != nil {
		return err
	}
	name := caps.CPUModel
	if name == "" {
		name = runtime.GOOS + "/" + runtime.GOARCH
	}
	fmt.Fprintf(a.Stdout, "Клиент для %s/%s. CPU: %s, логических CPU: %d.\n", runtime.GOOS, runtime.GOARCH, name, caps.CPULogical)
	hasCUDA := false
	for _, image := range target.Images {
		hasCUDA = hasCUDA || image.Accelerator == "cuda"
	}
	explicitGPU := false
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "gpu" {
			explicitGPU = true
		}
	})
	reader := bufio.NewReaderSize(a.Stdin, 8193)
	// Preserve buffered input: consent and the following invitation can arrive
	// together in a redirected stream, but are consumed at different stages.
	originalInput := a.Stdin
	a.Stdin = reader
	defer func() { a.Stdin = originalInput }()
	if len(caps.GPUs) > 0 {
		for _, device := range caps.GPUs {
			name := device.Name
			if name == "" {
				name = "NVIDIA " + device.ID
			}
			fmt.Fprintf(a.Stdout, "GPU: %s, VRAM %.1f GiB, драйвер %s.\n", name, float64(device.VRAMBytes)/(1<<30), device.Driver)
		}
		if !explicitGPU && !*check && hasCUDA {
			fmt.Fprint(a.Stdout, "Разрешить вычисления на NVIDIA и скачать CUDA-окружение? [y/N]: ")
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
				return errors.New("выберите y или n; CUDA не загружалась")
			}
		}
	} else if len(caps.NVIDIADeviceNames) > 0 {
		fmt.Fprintf(a.Stdout, "NVIDIA обнаружена: %s. Вычислительный драйвер пока недоступен; выбран CPU.\n", strings.Join(caps.NVIDIADeviceNames, ", "))
	}
	if *gpu && (len(caps.GPUs) == 0 || !hasCUDA) {
		return errors.New("для GPU нужны драйвер NVIDIA и поддерживаемое CUDA-окружение; используйте --gpu=false для CPU")
	}
	if *cache == "" {
		dir, e := os.UserCacheDir()
		if e != nil {
			return e
		}
		*cache = filepath.Join(dir, "Campus", "downloads")
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	fmt.Fprintf(a.Stdout, "Подготавливаю %s-окружение; проверенные файлы сохраняются в кэше.\n", map[bool]string{false: "CPU", true: "CPU/CUDA"}[*gpu])
	prepared, err := installer.Prepare(ctx, public.HTTP, *catalogURL, *catalogSHA, catalog, target, *gpu, *cache, executable)
	if err != nil {
		return err
	}
	options := []string{"--bundle", prepared.Root, "--manifest-sha256", prepared.ManifestSHA256, "--gpu=" + strconv.FormatBool(*gpu)}
	if *check {
		options = append(options, "--check-only")
	}
	if *managed {
		options = append(options, "--managed")
	}
	return a.connect(ctx, cfg, options)
}
