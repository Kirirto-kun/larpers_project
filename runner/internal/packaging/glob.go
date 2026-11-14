package packaging

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
)

// ValidateGlob accepts portable relative file globs. A double star matches
// complete directory segments only; it never broadens traversal or drive paths.
func ValidateGlob(pattern string) error {
	if pattern == "" || len(pattern) > 512 || strings.ContainsAny(pattern, "\\:\x00\r\n{}") || strings.HasPrefix(pattern, "/") || path.Clean(pattern) != pattern {
		return fmt.Errorf("unsafe data glob %q", pattern)
	}
	if len(strings.Split(pattern, "/")) > 32 || strings.Count(pattern, "**") > 4 || strings.Count(pattern, "*")+strings.Count(pattern, "?")+strings.Count(pattern, "[") > 64 || secret(pattern) {
		return fmt.Errorf("unsafe or unbounded data glob %q", pattern)
	}
	first := strings.ToLower(strings.Split(pattern, "/")[0])
	if first == "stages" || first == "project.zip" {
		return fmt.Errorf("data glob uses a reserved sandbox path %q", pattern)
	}
	for _, char := range pattern {
		if char < 32 || char == 127 || strings.ContainsRune("<>\"|", char) {
			return fmt.Errorf("nonportable data glob %q", pattern)
		}
	}
	for _, part := range strings.Split(pattern, "/") {
		if part == "." || part == ".." || strings.TrimRight(part, " .") != part {
			return fmt.Errorf("unsafe data glob %q", pattern)
		}
		if strings.Contains(part, "**") && part != "**" {
			return fmt.Errorf("double star must be a complete path segment: %q", pattern)
		}
		if !strings.ContainsAny(part, "*?[") {
			if err := SafePath(part); err != nil {
				return err
			}
		}
	}
	return nil
}

func MatchGlob(pattern, name string) bool {
	parts, names := strings.Split(pattern, "/"), strings.Split(name, "/")
	segments := make([][]globToken, len(parts))
	for i, part := range parts {
		if part != "**" {
			segments[i] = parseGlobSegment(part)
		}
	}
	known := map[[2]int]bool{}
	memo := map[[2]int]bool{}
	var match func(int, int) bool
	var calculate func(int, int) bool
	match = func(p, n int) bool {
		key := [2]int{p, n}
		if known[key] {
			return memo[key]
		}
		value := calculate(p, n)
		known[key] = true
		memo[key] = value
		return value
	}
	calculate = func(p, n int) bool {
		if p == len(parts) {
			return n == len(names)
		}
		if parts[p] == "**" {
			return match(p+1, n) || n < len(names) && match(p, n+1)
		}
		if n == len(names) {
			return false
		}
		return matchGlobSegment(segments[p], names[n]) && match(p+1, n+1)
	}
	return match(0, 0)
}

func SafeDataPath(name string) error {
	if err := SafePath(name); err != nil {
		return err
	}
	if len(name) > 512 || len(strings.Split(name, "/")) > 32 || secret(name) || strings.ContainsAny(name, "*?[]<>\"|") {
		return fmt.Errorf("unsafe declared data path %q", name)
	}
	first := strings.ToLower(strings.Split(name, "/")[0])
	if first == "stages" || first == "project.zip" {
		return fmt.Errorf("reserved data path %q", name)
	}
	for _, char := range name {
		if char < 32 || char == 127 {
			return fmt.Errorf("nonportable data path %q", name)
		}
	}
	return nil
}

// SelectFiles hashes only declared data files, while refusing symlink bindings,
// credentials, aliases and unmatched globs. It does not execute project code.
func SelectFiles(root string, patterns []string, r Rules) (ProjectManifest, error) {
	r = defaults(r)
	if len(patterns) > 32 {
		return ProjectManifest{}, errors.New("data requires at most 32 relative file globs")
	}
	if len(patterns) == 0 {
		return manifest(nil), nil
	}
	for _, pattern := range patterns {
		if err := ValidateGlob(pattern); err != nil {
			return ProjectManifest{}, err
		}
	}
	root, err := filepath.Abs(root)
	if err != nil {
		return ProjectManifest{}, err
	}
	st, err := os.Lstat(root)
	if err != nil {
		return ProjectManifest{}, err
	}
	if !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return ProjectManifest{}, errors.New("data root must be a regular directory")
	}
	rooted, err := os.OpenRoot(root)
	if err != nil {
		return ProjectManifest{}, err
	}
	defer rooted.Close()
	matched := make([]bool, len(patterns))
	seen := map[string]bool{}
	var files []File
	var total int64
	err = filepath.WalkDir(root, func(p string, d fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if p == root {
			return nil
		}
		rel, e := filepath.Rel(root, p)
		if e != nil {
			return e
		}
		name := filepath.ToSlash(rel)
		selected := false
		for i, pattern := range patterns {
			if MatchGlob(pattern, name) {
				selected = true
				if !d.IsDir() {
					matched[i] = true
				}
			}
		}
		if secret(name) || ignored(name, r) {
			if selected && !d.IsDir() {
				return fmt.Errorf("declared data includes a private or excluded path: %s", name)
			}
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.Type()&os.ModeSymlink != 0 {
			// Directory symlinks are never followed, including when a glob would
			// select a descendant rather than the link itself.
			return fmt.Errorf("symlink is not allowed in declared data inventory: %s", name)
		}
		if d.IsDir() || !selected {
			return nil
		}
		if e = SafeDataPath(name); e != nil {
			return e
		}
		if !d.Type().IsRegular() {
			return fmt.Errorf("non-regular data file: %s", name)
		}
		key := strings.ToLower(name)
		if seen[key] {
			return fmt.Errorf("case-colliding data file: %s", name)
		}
		seen[key] = true
		f, e := rooted.Open(filepath.FromSlash(name))
		if e != nil {
			return e
		}
		defer f.Close()
		st, e := f.Stat()
		if e != nil {
			return e
		}
		if !st.Mode().IsRegular() {
			return errors.New("data file changed type during inventory")
		}
		if len(files) >= r.MaxFiles || st.Size() > r.MaxBytes-total {
			return errors.New("declared data quota exceeded")
		}
		h := sha256.New()
		n, e := io.Copy(h, io.LimitReader(f, r.MaxBytes-total+1))
		if e != nil {
			return e
		}
		if n > r.MaxBytes-total || n != st.Size() {
			return errors.New("data changed or exceeds quota")
		}
		total += n
		files = append(files, File{Path: name, SizeBytes: n, Digest: hex.EncodeToString(h.Sum(nil))})
		return nil
	})
	if err != nil {
		return ProjectManifest{}, err
	}
	for i, ok := range matched {
		if !ok {
			return ProjectManifest{}, fmt.Errorf("data glob matched no safe files: %s", patterns[i])
		}
	}
	return manifest(files), nil
}
