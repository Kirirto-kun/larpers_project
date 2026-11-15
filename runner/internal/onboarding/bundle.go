// Package onboarding verifies an operator-prepared, pinned local connection kit.
package onboarding

import (
	"bytes"
	"campus/runner/internal/client"
	"campus/runner/internal/packaging"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type File struct {
	Path      string `json:"path"`
	SHA256    string `json:"sha256"`
	SizeBytes int64  `json:"size_bytes"`
}
type Image struct {
	File
	ImageDigest string `json:"image_digest"`
	Accelerator string `json:"accelerator"`
	Platform    string `json:"platform"`
}
type Manifest struct {
	SchemaVersion string  `json:"schema_version"`
	OS            string  `json:"os"`
	Arch          string  `json:"arch"`
	Runner        File    `json:"runner"`
	Profiles      File    `json:"profiles"`
	Images        []Image `json:"images"`
	CAFile        *File   `json:"ca_file,omitempty"`
	InviteFile    *File   `json:"invite_file,omitempty"`
	Endpoint      string  `json:"endpoint,omitempty"`
}
type Bundle struct {
	Root     string
	Manifest Manifest
}

func (b *Bundle) Path(file File) string { return filepath.Join(b.Root, filepath.FromSlash(file.Path)) }

func noLinks(path string) error {
	for {
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		if linked(info) {
			return errors.New("connection kit paths must not contain links or reparse points")
		}
		parent := filepath.Dir(path)
		if parent == path {
			return nil
		}
		path = parent
	}
}
func regular(path string, maxBytes int64) (*os.File, error) {
	if err := noLinks(path); err != nil {
		return nil, err
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Size() < 0 || st.Size() > maxBytes {
		f.Close()
		return nil, errors.New("kit input must be a bounded regular file")
	}
	return f, nil
}

type contextReader struct {
	ctx    context.Context
	source io.Reader
}

func (r contextReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	return r.source.Read(p)
}

func (b *Bundle) VerifyFile(ctx context.Context, file File) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	f, err := regular(b.Path(file), file.SizeBytes)
	if err != nil {
		return err
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.CopyBuffer(h, contextReader{ctx, f}, make([]byte, 256<<10))
	if err != nil {
		return err
	}
	if n != file.SizeBytes || hex.EncodeToString(h.Sum(nil)) != file.SHA256 {
		return fmt.Errorf("connection kit checksum/size mismatch: %s", file.Path)
	}
	return nil
}

func Load(ctx context.Context, root, expected, goos, arch string) (*Bundle, error) {
	expected = strings.ToLower(expected)
	if !client.DigestPattern.MatchString(expected) {
		return nil, errors.New("connection manifest needs its trusted SHA256")
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		return nil, err
	}
	f, err := regular(filepath.Join(absolute, "connection.json"), 64<<10)
	if err != nil {
		return nil, err
	}
	raw, err := io.ReadAll(io.LimitReader(contextReader{ctx, f}, (64<<10)+1))
	f.Close()
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != expected {
		return nil, errors.New("connection manifest checksum mismatch")
	}
	var manifest Manifest
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&manifest); err != nil {
		return nil, fmt.Errorf("invalid connection manifest: %w", err)
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, errors.New("connection manifest has trailing data")
	}
	if manifest.SchemaVersion != "1" || manifest.OS != goos || manifest.Arch != arch || (goos != "darwin" && goos != "windows" && goos != "linux") || (arch != "amd64" && arch != "arm64") {
		return nil, errors.New("connection kit does not match the native operating system/architecture")
	}
	if manifest.Endpoint != "" {
		if _, err = client.New(manifest.Endpoint, "", "", nil); err != nil {
			return nil, errors.New("invalid kit coordinator endpoint")
		}
	}
	b := &Bundle{Root: absolute, Manifest: manifest}
	files := []File{manifest.Runner, manifest.Profiles}
	limits := []int64{128 << 20, 2 << 20}
	if manifest.CAFile != nil {
		files = append(files, *manifest.CAFile)
		limits = append(limits, 2<<20)
	}
	if manifest.InviteFile != nil {
		files = append(files, *manifest.InviteFile)
		limits = append(limits, 2<<20)
	}
	if len(manifest.Images) < 1 || len(manifest.Images) > 2 {
		return nil, errors.New("kit needs one CPU image and at most one CUDA image")
	}
	accelerators := map[string]bool{}
	for _, image := range manifest.Images {
		if accelerators[image.Accelerator] || (image.Accelerator != "cpu" && image.Accelerator != "cuda") || image.Platform != "linux/"+arch || !client.ImagePattern.MatchString(image.ImageDigest) || (image.Accelerator == "cuda" && (arch != "amd64" || goos == "darwin")) {
			return nil, errors.New("invalid or duplicate native runtime image")
		}
		accelerators[image.Accelerator] = true
		files = append(files, image.File)
		limits = append(limits, 8<<30)
	}
	if !accelerators["cpu"] {
		return nil, errors.New("connection kit must include its CPU runtime")
	}
	var total int64
	for i, file := range files {
		if packaging.SafePath(file.Path) != nil || file.Path == "connection.json" || !client.DigestPattern.MatchString(file.SHA256) || file.SizeBytes < 1 || file.SizeBytes > limits[i] {
			return nil, errors.New("invalid file path/checksum/quota in connection manifest")
		}
		total += file.SizeBytes
		if total > 16<<30 {
			return nil, errors.New("connection kit exceeds total quota")
		}
		for _, prior := range files[:i] {
			if strings.EqualFold(prior.Path, file.Path) || strings.HasPrefix(strings.ToLower(prior.Path), strings.ToLower(file.Path)+"/") || strings.HasPrefix(strings.ToLower(file.Path), strings.ToLower(prior.Path)+"/") {
				return nil, errors.New("connection kit file paths collide")
			}
		}
		info, err := os.Lstat(b.Path(file))
		if err != nil {
			return nil, err
		}
		if linked(info) || !info.Mode().IsRegular() || info.Size() != file.SizeBytes {
			return nil, errors.New("kit file type or size differs from manifest")
		}
	}
	// Large unselected GPU archives are verified only if the owner opts in.
	for _, file := range files[:len(files)-len(manifest.Images)] {
		if err = b.VerifyFile(ctx, file); err != nil {
			return nil, err
		}
	}
	return b, nil
}

func (b *Bundle) VerifySelected(ctx context.Context, gpu bool) error {
	foundGPU := false
	for _, image := range b.Manifest.Images {
		if image.Accelerator == "cuda" {
			foundGPU = true
			if !gpu {
				continue
			}
		}
		if err := b.VerifyFile(ctx, image.File); err != nil {
			return err
		}
	}
	if gpu && !foundGPU {
		return errors.New("this connection kit has no approved CUDA runtime")
	}
	return nil
}
