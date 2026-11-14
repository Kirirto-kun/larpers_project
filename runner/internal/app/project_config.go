package app

import (
	"bytes"
	"campus/runner/internal/packaging"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

type projectConfig struct {
	SchemaVersion string         `json:"schema_version"`
	Name          string         `json:"name"`
	Data          []string       `json:"data"`
	Prompt        string         `json:"prompt,omitempty"`
	Stages        []projectStage `json:"stages"`
}
type projectStage struct {
	ID             string           `json:"id"`
	Kind           string           `json:"kind"`
	Inputs         []string         `json:"inputs"`
	DependsOn      []string         `json:"depends_on"`
	Command        []string         `json:"command"`
	Outputs        []projectOutput  `json:"outputs"`
	Accelerator    string           `json:"accelerator"`
	Resources      projectResources `json:"resources"`
	TimeoutSeconds int64            `json:"timeout_seconds"`
}
type projectResources struct {
	CPUMillis   int64 `json:"cpu_millis"`
	MemoryBytes int64 `json:"memory_bytes"`
	DiskBytes   int64 `json:"disk_bytes"`
	VRAMBytes   int64 `json:"vram_bytes"`
}
type projectOutput struct {
	Path     string `json:"path"`
	MaxBytes int64  `json:"max_bytes"`
	Required bool   `json:"required"`
}

func strictJSON(raw []byte, out any) error {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(out); err != nil {
		return err
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return errors.New("unexpected trailing JSON data")
	}
	return nil
}

func defaultProjectResources() projectResources {
	return projectResources{CPUMillis: 1000, MemoryBytes: 536870912, DiskBytes: 268435456, VRAMBytes: 1073741824}
}
func (r *projectResources) UnmarshalJSON(raw []byte) error {
	type plain projectResources
	value := plain(defaultProjectResources())
	if err := strictJSON(raw, &value); err != nil {
		return err
	}
	*r = projectResources(value)
	return nil
}
func (s *projectStage) UnmarshalJSON(raw []byte) error {
	type plain projectStage
	value := plain(projectStage{Inputs: []string{}, DependsOn: []string{}, Accelerator: "auto", Resources: defaultProjectResources(), TimeoutSeconds: 300})
	if err := strictJSON(raw, &value); err != nil {
		return err
	}
	*s = projectStage(value)
	return nil
}
func (o *projectOutput) UnmarshalJSON(raw []byte) error {
	type plain projectOutput
	value := plain(projectOutput{Required: true})
	if err := strictJSON(raw, &value); err != nil {
		return err
	}
	*o = projectOutput(value)
	return nil
}

var projectStageID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)

func loadProject(root string) (projectConfig, error) {
	var result projectConfig
	file := filepath.Join(root, "campus.json")
	st, err := os.Lstat(file)
	if err != nil {
		return result, err
	}
	if !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 || st.Size() > 262144 {
		return result, usageError("invalid project config: campus.json must be a regular JSON file of at most 256 KiB")
	}
	raw, err := os.ReadFile(file)
	if err != nil {
		return result, err
	}
	if !utf8.Valid(raw) {
		return result, usageError("invalid project config: campus.json must contain UTF-8 JSON")
	}
	if err = strictJSON(raw, &result); err != nil {
		return result, usageError("invalid project config: " + err.Error())
	}
	if err = validateProjectConfig(result); err != nil {
		return result, usageError("invalid project config: " + err.Error())
	}
	return result, nil
}

func validateProjectConfig(c projectConfig) error {
	if c.SchemaVersion != "1" {
		return errors.New("schema_version must be 1")
	}
	if strings.TrimSpace(c.Name) == "" || utf8.RuneCountInString(c.Name) > 256 || strings.ContainsAny(c.Name, "\x00\r\n") {
		return errors.New("name must contain 1–256 characters")
	}
	if utf8.RuneCountInString(c.Prompt) > 8000 || strings.ContainsRune(c.Prompt, 0) {
		return errors.New("prompt exceeds 8000 characters or contains NUL")
	}
	if c.Data == nil || len(c.Data) > 32 {
		return errors.New("data requires an array of at most 32 file globs")
	}
	dataPatterns := map[string]bool{}
	for _, pattern := range c.Data {
		if err := packaging.ValidateGlob(pattern); err != nil {
			return err
		}
		key := strings.ToLower(pattern)
		if dataPatterns[key] {
			return errors.New("data globs must be unique without case aliases")
		}
		dataPatterns[key] = true
	}
	if len(c.Stages) == 0 || len(c.Stages) > 24 {
		return errors.New("1–24 stages are required")
	}
	seen := map[string]bool{}
	exactIDs := map[string]bool{}
	for _, s := range c.Stages {
		if !projectStageID.MatchString(s.ID) || seen[strings.ToLower(s.ID)] {
			return errors.New("stage IDs must be unique portable identifiers")
		}
		if s.Kind != "map" && s.Kind != "reduce" {
			return fmt.Errorf("stage %s must be map or reduce", s.ID)
		}
		if len(s.Inputs) > 32 || s.Kind == "map" && len(s.Inputs) == 0 {
			return fmt.Errorf("stage %s: maps require data input globs and each stage accepts at most 32 globs", s.ID)
		}
		inputPatterns := map[string]bool{}
		for _, pattern := range s.Inputs {
			if err := packaging.ValidateGlob(pattern); err != nil {
				return err
			}
			key := strings.ToLower(pattern)
			if inputPatterns[key] {
				return errors.New("stage input globs must be unique without case aliases")
			}
			inputPatterns[key] = true
		}
		deps := map[string]bool{}
		for _, id := range s.DependsOn {
			if !exactIDs[id] || deps[id] {
				return fmt.Errorf("stage %s: dependencies must name distinct earlier stages", s.ID)
			}
			deps[id] = true
		}
		if len(s.Command) == 0 || len(s.Command) > 64 {
			return fmt.Errorf("stage %s requires 1–64 literal argv elements", s.ID)
		}
		total := 0
		for _, arg := range s.Command {
			total += len(arg)
			if utf8.RuneCountInString(arg) > 4096 || strings.ContainsRune(arg, 0) {
				return fmt.Errorf("stage %s has an invalid or oversized argument", s.ID)
			}
			if s.Kind != "map" && strings.Contains(arg, "{input}") {
				return errors.New("{input} is allowed only in map argv")
			}
			remaining := strings.ReplaceAll(strings.ReplaceAll(arg, "{input}", ""), "{accelerator}", "")
			if strings.ContainsAny(remaining, "{}") {
				return errors.New("only {input} and {accelerator} argv substitutions are supported")
			}
		}
		if s.Command[0] == "" || strings.ContainsAny(s.Command[0], "{}") || total > 65536 {
			return fmt.Errorf("stage %s argv exceeds its bound", s.ID)
		}
		if s.Accelerator != "auto" && s.Accelerator != "cpu" && s.Accelerator != "cuda" {
			return errors.New("accelerator must be auto, cpu or cuda")
		}
		r := s.Resources
		if r.CPUMillis < 1 || r.CPUMillis > 4000 || r.MemoryBytes < 32<<20 || r.MemoryBytes > 4<<30 || r.DiskBytes < 1<<20 || r.DiskBytes > 512<<20 || r.VRAMBytes < 0 || r.VRAMBytes > 4<<30 || s.Accelerator == "cuda" && r.VRAMBytes == 0 {
			return fmt.Errorf("stage %s resources exceed the supported bounds", s.ID)
		}
		if s.TimeoutSeconds < 1 || s.TimeoutSeconds > 86400 {
			return errors.New("timeout_seconds must be 1–86400")
		}
		if len(s.Outputs) == 0 || len(s.Outputs) > 32 {
			return errors.New("each stage requires 1–32 bounded outputs")
		}
		outputs := map[string]bool{}
		var outputBytes int64
		for _, out := range s.Outputs {
			if err := packaging.SafePath(out.Path); err != nil {
				return err
			}
			if strings.EqualFold(strings.SplitN(out.Path, "/", 2)[0], "stages") {
				return errors.New("output first component stages is reserved for distributed result downloads")
			}
			key := strings.ToLower(out.Path)
			if outputs[key] || len(out.Path) > 512 || out.MaxBytes < 1 || out.MaxBytes > 512<<20-outputBytes {
				return errors.New("outputs collide or exceed the disk bound")
			}
			outputs[key] = true
			outputBytes += out.MaxBytes
		}
		for key := range outputs {
			for parent := filepath.ToSlash(filepath.Dir(filepath.FromSlash(key))); parent != "."; parent = filepath.ToSlash(filepath.Dir(filepath.FromSlash(parent))) {
				if outputs[parent] {
					return errors.New("output file/directory paths collide")
				}
			}
		}
		temporary := max(int64(4096), min(int64(64<<20), r.DiskBytes/8))
		if outputBytes+2*temporary >= r.DiskBytes {
			return errors.New("declared outputs and sandbox temporary quotas exceed reserved disk")
		}
		seen[strings.ToLower(s.ID)] = true
		exactIDs[s.ID] = true
	}
	return nil
}

func validateProjectData(c projectConfig, data packaging.ProjectManifest) error {
	for _, s := range c.Stages {
		for _, pattern := range s.Inputs {
			count := 0
			for _, file := range data.Files {
				if packaging.MatchGlob(pattern, file.Path) {
					count++
				}
			}
			if count == 0 {
				return usageError(fmt.Sprintf("invalid project config: stage %s input glob %s selects no declared data", s.ID, pattern))
			}
		}
	}
	return nil
}
