// Package packaging creates bounded, deterministic snapshots without executing project code.
package packaging

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

type Rules struct {
	MaxFiles int
	MaxBytes int64
	Ignore   []string
}
type File struct {
	Path      string `json:"path"`
	SizeBytes int64  `json:"size_bytes"`
	Digest    string `json:"digest"`
}
type ProjectManifest struct {
	SchemaVersion string `json:"schema_version"`
	Files         []File `json:"files"`
	TotalBytes    int64  `json:"total_bytes"`
	Digest        string `json:"digest"`
}
type ResultManifest = ProjectManifest

func defaults(r Rules) Rules {
	if r.MaxFiles <= 0 {
		r.MaxFiles = 10000
	}
	if r.MaxBytes <= 0 {
		r.MaxBytes = 1 << 30
	}
	return r
}

// SafePath deliberately rejects platform-specific aliases as well as traversal.
func SafePath(name string) error {
	if name == "" || strings.ContainsAny(name, "\\:\x00") || strings.HasPrefix(name, "/") || path.Clean(name) != name {
		return fmt.Errorf("unsafe relative path %q", name)
	}
	for _, p := range strings.Split(name, "/") {
		if p == "." || p == ".." || strings.TrimRight(p, " .") != p {
			return fmt.Errorf("unsafe path component %q", p)
		}
		base := strings.ToUpper(strings.SplitN(p, ".", 2)[0])
		if base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" || regexp.MustCompile(`^(COM|LPT)[1-9]$`).MatchString(base) {
			return fmt.Errorf("reserved path %q", p)
		}
	}
	return nil
}

func secret(name string) bool {
	for _, part := range strings.Split(strings.ToLower(name), "/") {
		if part == ".git" || part == ".hg" || part == ".svn" || part == ".venv" || part == "venv" || part == "node_modules" || part == "__pycache__" || part == ".ssh" || part == ".aws" || part == ".azure" || part == ".config" || part == ".campus" || part == ".env" || strings.HasPrefix(part, ".env.") || strings.HasPrefix(part, "id_rsa") || strings.HasPrefix(part, "id_ed25519") || part == "credentials.json" || part == "credentials" || part == ".netrc" || part == ".npmrc" || strings.HasSuffix(part, ".pem") || strings.HasSuffix(part, ".key") || strings.HasSuffix(part, ".p12") {
			return true
		}
	}
	return false
}

func patternMatch(pattern, name string) bool {
	pattern = strings.TrimPrefix(pattern, "/")
	dir := strings.HasSuffix(pattern, "/")
	pattern = strings.TrimSuffix(pattern, "/")
	if dir && (name == pattern || strings.HasPrefix(name, pattern+"/")) {
		return true
	}
	if !strings.Contains(pattern, "/") {
		for _, part := range strings.Split(name, "/") {
			if ok, _ := path.Match(pattern, part); ok {
				return true
			}
		}
	}
	if ok, _ := path.Match(pattern, name); ok {
		return true
	}
	// ** matches directories, unlike path.Match's single-component *.
	if strings.Contains(pattern, "**") {
		s := regexp.QuoteMeta(pattern)
		s = strings.ReplaceAll(s, `\*\*/`, `(?:.*/)?`)
		s = strings.ReplaceAll(s, `\*\*`, `.*`)
		s = strings.ReplaceAll(s, `\*`, `[^/]*`)
		s = strings.ReplaceAll(s, `\?`, `[^/]`)
		ok, _ := regexp.MatchString("^"+s+"$", name)
		return ok
	}
	return false
}

func ignored(name string, r Rules) bool {
	if secret(name) {
		return true
	}
	skip := false
	for _, line := range r.Ignore {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		neg := strings.HasPrefix(line, "!")
		if neg {
			line = line[1:]
		}
		if patternMatch(line, name) {
			skip = !neg
		}
	}
	return skip
}

func manifest(files []File) ProjectManifest {
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
	if files == nil {
		files = []File{}
	}
	m := ProjectManifest{SchemaVersion: "1", Files: files}
	for _, f := range files {
		m.TotalBytes += f.SizeBytes
	}
	b, _ := json.Marshal(m)
	h := sha256.Sum256(b)
	m.Digest = hex.EncodeToString(h[:])
	return m
}

func Scan(root string, r Rules) (ProjectManifest, error) {
	r = defaults(r)
	root, err := filepath.Abs(root)
	if err != nil {
		return ProjectManifest{}, err
	}
	rooted, err := os.OpenRoot(root)
	if err != nil {
		return ProjectManifest{}, err
	}
	defer rooted.Close()
	if f, err := rooted.Open(".campusignore"); err == nil {
		data, e := io.ReadAll(io.LimitReader(f, 65537))
		f.Close()
		if e != nil || len(data) > 65536 {
			return ProjectManifest{}, errors.New(".campusignore exceeds 64 KiB")
		}
		r.Ignore = append(strings.Split(string(data), "\n"), r.Ignore...)
	}
	var files []File
	var total int64
	seen := map[string]bool{}
	err = filepath.WalkDir(root, func(p string, d fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if p == root {
			return nil
		}
		rel, e := filepath.Rel(root, p)
		if e != nil {
			return e
		}
		name := filepath.ToSlash(rel)
		if ignored(name, r) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if e = SafePath(name); e != nil {
			return e
		}
		if d.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlink is not allowed: %s", name)
		}
		if d.IsDir() {
			return nil
		}
		if !d.Type().IsRegular() {
			return fmt.Errorf("non-regular file: %s", name)
		}
		if seen[strings.ToLower(name)] {
			return fmt.Errorf("case-colliding file: %s", name)
		}
		seen[strings.ToLower(name)] = true
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
			return errors.New("file changed type during snapshot")
		}
		if len(files) >= r.MaxFiles || st.Size() > r.MaxBytes-total {
			return errors.New("project quota exceeded")
		}
		h := sha256.New()
		n, e := io.Copy(h, io.LimitReader(f, r.MaxBytes-total+1))
		if e != nil {
			return e
		}
		if n > r.MaxBytes-total {
			return errors.New("project changed or exceeds quota")
		}
		total += n
		files = append(files, File{Path: name, SizeBytes: n, Digest: hex.EncodeToString(h.Sum(nil))})
		return nil
	})
	if err != nil {
		return ProjectManifest{}, err
	}
	return manifest(files), nil
}

func PackageProject(root string, r Rules, destination io.Writer) (ProjectManifest, error) {
	m, err := Scan(root, r)
	if err != nil {
		return m, err
	}
	rooted, err := os.OpenRoot(root)
	if err != nil {
		return m, err
	}
	defer rooted.Close()
	z := zip.NewWriter(destination)
	for _, entry := range m.Files {
		f, err := rooted.Open(filepath.FromSlash(entry.Path))
		if err != nil {
			z.Close()
			return m, err
		}
		header := &zip.FileHeader{Name: entry.Path, Method: zip.Deflate}
		header.SetMode(0600)
		header.SetModTime(time.Date(1980, 1, 1, 0, 0, 0, 0, time.UTC))
		w, err := z.CreateHeader(header)
		if err != nil {
			f.Close()
			z.Close()
			return m, err
		}
		h := sha256.New()
		n, err := io.Copy(io.MultiWriter(w, h), io.LimitReader(f, entry.SizeBytes+1))
		f.Close()
		if err != nil || n != entry.SizeBytes || hex.EncodeToString(h.Sum(nil)) != entry.Digest {
			z.Close()
			return m, errors.New("project changed while packaging; retry snapshot")
		}
	}
	return m, z.Close()
}

func Extract(source io.ReaderAt, size int64, destination string, r Rules) error {
	r = defaults(r)
	z, err := zip.NewReader(source, size)
	if err != nil {
		return err
	}
	if len(z.File) > r.MaxFiles {
		return errors.New("archive file quota exceeded")
	}
	var total uint64
	seen := map[string]bool{}
	for _, f := range z.File {
		name := strings.TrimSuffix(f.Name, "/")
		if err := SafePath(name); err != nil {
			return err
		}
		if f.Mode()&os.ModeSymlink != 0 || (!f.Mode().IsRegular() && !f.FileInfo().IsDir()) {
			return errors.New("archive special files are forbidden")
		}
		key := strings.ToLower(name)
		if seen[key] {
			return errors.New("duplicate archive path")
		}
		seen[key] = true
		if f.UncompressedSize64 > uint64(r.MaxBytes)-total {
			return errors.New("archive byte quota exceeded")
		}
		total += f.UncompressedSize64
	}
	if err := os.MkdirAll(destination, 0700); err != nil {
		return err
	}
	root, err := os.OpenRoot(destination)
	if err != nil {
		return err
	}
	defer root.Close()
	var actual int64
	for _, f := range z.File {
		name := filepath.FromSlash(strings.TrimSuffix(f.Name, "/"))
		if f.FileInfo().IsDir() {
			if err := root.MkdirAll(name, 0700); err != nil {
				return err
			}
			continue
		}
		if parent := filepath.Dir(name); parent != "." {
			if err := root.MkdirAll(parent, 0700); err != nil {
				return err
			}
		}
		in, err := f.Open()
		if err != nil {
			return err
		}
		out, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			in.Close()
			return err
		}
		n, e := io.Copy(out, io.LimitReader(in, r.MaxBytes-actual+1))
		closeErr := out.Close()
		in.Close()
		actual += n
		if e != nil {
			return e
		}
		if closeErr != nil {
			return closeErr
		}
		if actual > r.MaxBytes || uint64(n) != f.UncompressedSize64 {
			return errors.New("archive size mismatch")
		}
	}
	return nil
}

func CollectOutputs(root string, before ProjectManifest, r Rules, declared []string) (ResultManifest, error) {
	current, err := Scan(root, r)
	if err != nil {
		return current, err
	}
	old := map[string]string{}
	for _, f := range before.Files {
		old[f.Path] = f.Digest
	}
	found := make([]bool, len(declared))
	var files []File
	for _, f := range current.Files {
		include := old[f.Path] != f.Digest
		for i, p := range declared {
			if err := SafePath(p); err != nil {
				return current, err
			}
			if f.Path == p || strings.HasPrefix(f.Path, p+"/") {
				include = true
				found[i] = true
			}
		}
		if include {
			files = append(files, f)
		}
	}
	for i, ok := range found {
		if !ok {
			return current, fmt.Errorf("declared output not found: %s", declared[i])
		}
	}
	return manifest(files), nil
}
