package containers

import (
	"campus/runner/internal/client"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type Paths struct {
	Inputs, Control, State, Export string
	Data, Resume                   string
}
type Docker struct{ InstallationID string }

func Name(installation, attempt string) string { return "campus-" + installation + "-" + attempt }
func mount(source, target string, ro bool) (string, error) {
	if source == "" || strings.ContainsAny(source, "\x00\r\n") {
		return "", errors.New("invalid bind source")
	}
	var b strings.Builder
	w := csv.NewWriter(&b)
	fields := []string{"type=bind", "src=" + source, "dst=" + target}
	if ro {
		fields = append(fields, "readonly")
	}
	if e := w.Write(fields); e != nil {
		return "", e
	}
	w.Flush()
	return strings.TrimSuffix(b.String(), "\n"), w.Error()
}
func BuildArgs(a client.Assignment, p Paths, installation, attempt string) ([]string, error) {
	if a.NetworkPolicy != "none" || !client.ImagePattern.MatchString(a.ImageDigest) {
		return nil, errors.New("unapproved network/image policy")
	}
	if !client.IDPattern.MatchString(installation) || !client.IDPattern.MatchString(attempt) {
		return nil, errors.New("invalid container identity")
	}
	r := a.ResourceLimits
	args := []string{"create", "--name", Name(installation, attempt), "--label", "io.campus.installation=" + installation, "--label", "io.campus.attempt=" + attempt, "--pull=never", "--restart=no", "--platform", a.Platform, "--network=none", "--read-only", "--user=0:0", "--cap-drop=ALL", "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=KILL", "--cap-add=CHOWN", "--cap-add=DAC_OVERRIDE", "--security-opt=no-new-privileges:true", "--pids-limit=" + strconv.FormatInt(r.PIDs, 10), "--memory=" + strconv.FormatInt(r.MemoryBytes, 10), "--memory-swap=" + strconv.FormatInt(r.MemoryBytes, 10), "--cpus=" + strconv.FormatFloat(float64(r.CPUMillis)/1000, 'f', 3, 64), "--log-driver=local", "--log-opt=max-size=4m", "--log-opt=max-file=1", "--log-opt=compress=false"}
	for _, volume := range []struct {
		target string
		size   int64
		noexec bool
	}{{"/workspace", r.WorkspaceBytes, false}, {"/outputs", r.OutputBytes, true}, {"/checkpoints", r.CheckpointBytes, true}, {"/tmp", r.TmpBytes, false}} {
		if volume.size <= 0 {
			return nil, errors.New("tmpfs quotas are required")
		}
		options := fmt.Sprintf("%s:rw,nosuid,nodev,size=%d,nr_inodes=12000,uid=0,gid=0,mode=0700", volume.target, volume.size)
		if volume.noexec {
			options += ",noexec"
		}
		args = append(args, "--tmpfs", options)
	}
	for _, v := range []struct {
		source, target string
		ro             bool
	}{{p.Inputs, "/inputs", true}, {p.Control, "/run/campus-lease", true}, {p.State, "/run/campus-state", false}, {p.Export, "/export", false}, {p.Data, "/data", true}, {p.Resume, "/resume", true}} {
		if v.source == "" && (v.target == "/data" || v.target == "/resume") {
			continue
		}
		m, e := mount(v.source, v.target, v.ro)
		if e != nil {
			return nil, e
		}
		args = append(args, "--mount", m)
	}
	if len(r.GPUIDs) > 0 {
		for _, id := range r.GPUIDs {
			if !client.IDPattern.MatchString(id) {
				return nil, errors.New("invalid GPU selector")
			}
		}
		args = append(args, "--gpus", `"device=`+strings.Join(r.GPUIDs, ",")+`"`)
	}
	args = append(args, "--entrypoint=/opt/campus/.venv/bin/python", a.ImageDigest, "-m", "environments.guard.guard")
	return args, nil
}

func run(ctx context.Context, args ...string) ([]byte, error) {
	bounded, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	out, e := exec.CommandContext(bounded, "docker", args...).CombinedOutput()
	if e != nil {
		message := strings.TrimSpace(string(out))
		if len(message) > 8192 {
			message = message[:8192]
		}
		return out, fmt.Errorf("docker %s failed: %w: %s", args[0], e, message)
	}
	return out, nil
}
func (d Docker) VerifyImage(ctx context.Context, image, platform string) error {
	if !client.ImagePattern.MatchString(image) {
		return errors.New("image must be immutable")
	}
	b, e := run(ctx, "image", "inspect", image)
	if e != nil {
		return errors.New("approved image is not cached; prepare it before joining")
	}
	var images []struct {
		OS     string `json:"Os"`
		Arch   string `json:"Architecture"`
		Config struct{ Labels map[string]string }
	}
	if e = json.Unmarshal(b, &images); e != nil {
		return e
	}
	if len(images) != 1 || images[0].OS+"/"+images[0].Arch != platform || images[0].Config.Labels["io.campus.guard"] != "1" {
		return errors.New("image architecture or protected PID1 guard contract is unsupported")
	}
	return nil
}
func (d Docker) Create(ctx context.Context, a client.Assignment, p Paths) (string, error) {
	args, e := BuildArgs(a, p, d.InstallationID, a.AttemptID)
	if e != nil {
		return "", e
	}
	_, e = run(ctx, args...)
	return Name(d.InstallationID, a.AttemptID), e
}
func (d Docker) Start(ctx context.Context, name string) error {
	_, e := run(ctx, "start", name)
	return e
}
func (d Docker) Wait(ctx context.Context, name string) error {
	out, e := exec.CommandContext(ctx, "docker", "wait", name).Output()
	if e != nil {
		return e
	}
	code, e := strconv.Atoi(strings.TrimSpace(string(out)))
	if e != nil {
		return e
	}
	if code != 0 {
		return fmt.Errorf("container exited with code %d", code)
	}
	return nil
}
func (d Docker) Stop(ctx context.Context, name string) error {
	_, e := run(ctx, "stop", "--time=2", name)
	return e
}
func (d Docker) Remove(ctx context.Context, name string) error {
	_, e := run(ctx, "rm", "--force", name)
	return e
}
func (d Docker) Logs(ctx context.Context, name string, w io.Writer) error {
	cmd := exec.CommandContext(ctx, "docker", "logs", "--follow", name)
	cmd.Stdout = w
	cmd.Stderr = w
	return cmd.Run()
}
func (d Docker) Stats(ctx context.Context, name string) (map[string]any, error) {
	b, e := run(ctx, "stats", "--no-stream", "--format", "{{json .}}", name)
	if e != nil {
		return nil, e
	}
	var values map[string]any
	e = json.Unmarshal(b, &values)
	return values, e
}
func (d Docker) Cleanup(ctx context.Context) error {
	b, e := run(ctx, "ps", "-aq", "--filter", "label=io.campus.installation="+d.InstallationID)
	if e != nil {
		return e
	}
	for _, id := range strings.Fields(string(b)) {
		if !client.IDPattern.MatchString(id) {
			return errors.New("invalid Docker container id")
		}
		details, e := run(ctx, "inspect", "--format", "{{index .Config.Labels \"io.campus.installation\"}}", id)
		if e != nil {
			return e
		}
		if strings.TrimSpace(string(details)) != d.InstallationID {
			return errors.New("refusing to remove another installation's container")
		}
		if e = d.Remove(ctx, id); e != nil {
			return e
		}
	}
	return nil
}
