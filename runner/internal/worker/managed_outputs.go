package worker

import (
	"campus/runner/internal/packaging"
	"errors"
	"strings"
)

type selectedResult struct {
	packaging.File
	SourcePath string
}

func selectManagedOutputs(files []packaging.File, required, optional []string) ([]selectedResult, error) {
	outputs := append(append([]string{}, required...), optional...)
	declared := map[string]bool{}
	for _, path := range outputs {
		if e := packaging.SafePath(path); e != nil {
			return nil, e
		}
		key := strings.ToLower(path)
		if declared[key] {
			return nil, errors.New("duplicate declared output")
		}
		declared[key] = true
	}
	selected := []selectedResult{}
	seen := map[string]bool{}
	names := map[string]bool{}
	for _, file := range files {
		if e := packaging.SafePath(file.Path); e != nil {
			return nil, e
		}
		source := file.Path
		key := strings.ToLower(source)
		if seen[key] {
			return nil, errors.New("duplicate guard output path")
		}
		seen[key] = true
		name := source
		if strings.HasPrefix(name, "workspace/") {
			name = strings.TrimPrefix(name, "workspace/")
		} else if strings.HasPrefix(name, "outputs/") {
			name = strings.TrimPrefix(name, "outputs/")
		}
		if !declared[strings.ToLower(name)] {
			continue
		}
		if names[strings.ToLower(name)] {
			return nil, errors.New("ambiguous declared output from workspace and outputs")
		}
		names[strings.ToLower(name)] = true
		// Preserve the exact declared spelling for server-side plan validation.
		for _, declaredName := range outputs {
			if strings.EqualFold(name, declaredName) {
				name = declaredName
				break
			}
		}
		file.Path = name
		selected = append(selected, selectedResult{File: file, SourcePath: source})
	}
	for _, path := range required {
		if !names[strings.ToLower(path)] {
			return nil, errors.New("required output missing from guard result")
		}
	}
	return selected, nil
}
