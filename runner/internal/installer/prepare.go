package installer

import (
	"bytes"
	"campus/runner/internal/onboarding"
	"campus/runner/internal/session"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"time"
)

type Prepared struct{ Root, ManifestSHA256 string }

// Prepare materializes only the selected verified artifacts. It never enrolls a
// device, executes an image, or creates an installation identity. cacheDir must
// explicitly identify a nonempty cache path.
func Prepare(ctx context.Context, h *http.Client, catalogURL, expectedSHA string, catalog *Catalog, target Target, gpu bool, cacheDir, runningBinary string) (Prepared, error) {
	if err := ctx.Err(); err != nil {
		return Prepared{}, err
	}
	if cacheDir == "" {
		return Prepared{}, errors.New("public preparation requires an explicit cache directory")
	}
	if catalog == nil || len(catalog.raw) == 0 || catalog.url != catalogURL || catalog.digest != expectedSHA {
		return Prepared{}, errors.New("preparation requires the fetched, pinned public catalog")
	}
	frozen, err := decodeCatalog(catalog.raw)
	if err != nil {
		return Prepared{}, err
	}
	public, _ := json.Marshal(catalog)
	approved, _ := json.Marshal(frozen)
	if !bytes.Equal(public, approved) {
		return Prepared{}, errors.New("public catalog changed after verification")
	}
	selected, err := frozen.Select(target.OS, target.Arch)
	if err != nil {
		return Prepared{}, err
	}
	if !reflect.DeepEqual(selected, target) {
		return Prepared{}, errors.New("selected target differs from the pinned public catalog")
	}
	images := make([]onboarding.Image, 0, 2)
	hasGPU := false
	for _, image := range selected.Images {
		if image.Accelerator == "cuda" {
			hasGPU = true
			if !gpu {
				continue
			}
		}
		images = append(images, image)
	}
	if gpu && !hasGPU {
		return Prepared{}, errors.New("public target has no approved CUDA runtime")
	}
	// Always verify the executable actually running, including on cache hits.
	if err = verifyPath(ctx, runningBinary, selected.Runner); err != nil {
		return Prepared{}, errors.New("running binary does not match the pinned public target")
	}
	cacheDir, err = filepath.Abs(cacheDir)
	if err != nil {
		return Prepared{}, err
	}
	if err = privateDir(cacheDir); err != nil {
		return Prepared{}, err
	}
	lockPath := filepath.Join(cacheDir, "runner.lock")
	if _, err = os.Lstat(lockPath); err == nil {
		if err = checkRegular(lockPath, 1); err != nil {
			return Prepared{}, err
		}
	} else if !os.IsNotExist(err) {
		return Prepared{}, err
	}
	unlock, err := session.Lock(cacheDir)
	if err != nil {
		return Prepared{}, errors.New("public download cache is already in use or unavailable")
	}
	defer unlock()
	objects, bundles := filepath.Join(cacheDir, "objects"), filepath.Join(cacheDir, "bundles")
	if err = privateDir(objects); err != nil {
		return Prepared{}, err
	}
	if err = privateDir(bundles); err != nil {
		return Prepared{}, err
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()
	if _, err = localObject(ctx, objects, runningBinary, selected.Runner); err != nil {
		return Prepared{}, err
	}
	files := []onboarding.File{selected.Profiles}
	if frozen.CAFile != nil {
		files = append(files, *frozen.CAFile)
	}
	for _, image := range images {
		files = append(files, image.File)
	}
	for _, file := range files {
		if _, err = downloadObject(ctx, h, catalogURL, objects, file); err != nil {
			return Prepared{}, err
		}
	}
	profileData, err := selectedProfiles(ctx, filepath.Join(objects, selected.Profiles.SHA256), selected.Profiles, selected.Images, images)
	if err != nil {
		return Prepared{}, err
	}
	profiles := selected.Profiles
	profiles.SizeBytes = int64(len(profileData))
	sum := sha256.Sum256(profileData)
	profiles.SHA256 = hex.EncodeToString(sum[:])
	manifest := onboarding.Manifest{SchemaVersion: "1", OS: selected.OS, Arch: selected.Arch, Runner: selected.Runner, Profiles: profiles, CAFile: frozen.CAFile, Endpoint: frozen.Endpoint, Images: images}
	manifestData, err := json.Marshal(manifest)
	if err != nil {
		return Prepared{}, err
	}
	manifestSum := sha256.Sum256(manifestData)
	manifestSHA := hex.EncodeToString(manifestSum[:])
	stage, err := session.PrivateTempDir(bundles, ".prepare-")
	if err != nil {
		return Prepared{}, err
	}
	defer removeStage(bundles, stage)
	for _, file := range append([]onboarding.File{selected.Runner}, files...) {
		destination := filepath.Join(stage, filepath.FromSlash(file.Path))
		if err = privateDir(filepath.Dir(destination)); err != nil {
			return Prepared{}, err
		}
		if file.Path == selected.Profiles.Path {
			err = os.WriteFile(destination, profileData, 0600)
		} else {
			imageFile := false
			for _, image := range images {
				if image.File == file {
					imageFile = true
				}
			}
			source := filepath.Join(objects, file.SHA256)
			if imageFile {
				err = os.Link(source, destination)
			} else {
				err = errors.New("copy metadata")
			}
			if err != nil {
				err = copyVerified(ctx, source, destination, file, file.Path == selected.Runner.Path)
			}
		}
		if err != nil {
			return Prepared{}, err
		}
	}
	if err = os.WriteFile(filepath.Join(stage, "connection.json"), manifestData, 0600); err != nil {
		return Prepared{}, err
	}
	bundle, err := onboarding.Load(ctx, stage, manifestSHA, selected.OS, selected.Arch)
	if err != nil {
		return Prepared{}, err
	}
	if err = bundle.VerifySelected(ctx, gpu); err != nil {
		return Prepared{}, err
	}
	choice := "cpu"
	if gpu {
		choice = "gpu"
	}
	destination := filepath.Join(bundles, expectedSHA+"-"+selected.OS+"-"+selected.Arch+"-"+choice)
	if _, err = os.Lstat(destination); err == nil {
		// A same-version bundle has a fixed allowlist. Never recursively delete
		// an existing directory or remove an unexpected user file.
		if err = removeBundle(destination, manifest); err != nil {
			return Prepared{}, err
		}
	} else if !os.IsNotExist(err) {
		return Prepared{}, err
	}
	if err = ctx.Err(); err != nil {
		return Prepared{}, err
	}
	if err = os.Rename(stage, destination); err != nil {
		return Prepared{}, err
	}
	return Prepared{Root: destination, ManifestSHA256: manifestSHA}, nil
}

func removeStage(parent, stage string) {
	rel, err := filepath.Rel(parent, stage)
	if err == nil && !strings.ContainsAny(rel, "/\\") && strings.HasPrefix(rel, ".prepare-") && noLinks(stage) == nil {
		_ = os.RemoveAll(stage)
	}
}

func manifestFiles(m onboarding.Manifest) []onboarding.File {
	files := []onboarding.File{m.Runner, m.Profiles}
	if m.CAFile != nil {
		files = append(files, *m.CAFile)
	}
	for _, image := range m.Images {
		files = append(files, image.File)
	}
	return files
}

func removeBundle(root string, m onboarding.Manifest) error {
	if err := noLinks(root); err != nil {
		return err
	}
	allowed := map[string]bool{"connection.json": true}
	dirs := map[string]bool{}
	for _, file := range manifestFiles(m) {
		allowed[file.Path] = true
		for dir := filepath.ToSlash(filepath.Dir(filepath.FromSlash(file.Path))); dir != "."; dir = filepath.ToSlash(filepath.Dir(filepath.FromSlash(dir))) {
			dirs[dir] = true
		}
	}
	var paths []string
	err := filepath.WalkDir(root, func(name string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if name == root {
			return nil
		}
		rel, err := filepath.Rel(root, name)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		info, err := os.Lstat(name)
		if err != nil {
			return err
		}
		if linked(info) || d.IsDir() && !dirs[rel] || !d.IsDir() && (!info.Mode().IsRegular() || !allowed[rel]) {
			return errors.New("existing public bundle contains unexpected files or links")
		}
		paths = append(paths, name)
		return nil
	})
	if err != nil {
		return err
	}
	sort.Slice(paths, func(i, j int) bool { return len(paths[i]) > len(paths[j]) })
	for _, name := range paths {
		if err = os.Remove(name); err != nil {
			return err
		}
	}
	return os.Remove(root)
}

func localObject(ctx context.Context, objects, source string, file onboarding.File) (string, error) {
	destination := filepath.Join(objects, file.SHA256)
	if ok, err := cached(ctx, destination, file); err != nil || ok {
		return destination, err
	}
	f, err := os.CreateTemp(objects, ".runner-")
	if err != nil {
		return "", err
	}
	tmp := f.Name()
	f.Close()
	defer os.Remove(tmp)
	if err = copyVerified(ctx, source, tmp, file, false); err != nil {
		return "", err
	}
	if err = os.Rename(tmp, destination); err != nil {
		return "", err
	}
	return destination, nil
}

var errChecksum = errors.New("public release file checksum/size mismatch")

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

func checkRegular(name string, maxBytes int64) error {
	if err := noLinks(name); err != nil {
		return err
	}
	st, err := os.Lstat(name)
	if err != nil {
		return err
	}
	if !st.Mode().IsRegular() || st.Size() < 0 || st.Size() > maxBytes {
		return errors.New("public cache/input must be a bounded regular file")
	}
	return nil
}
func openRegular(name string, maxBytes int64) (*os.File, error) {
	if err := checkRegular(name, maxBytes); err != nil {
		return nil, err
	}
	before, err := os.Lstat(name)
	if err != nil {
		return nil, err
	}
	f, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	after, err := f.Stat()
	if err != nil || !os.SameFile(before, after) {
		f.Close()
		return nil, errors.New("public cache/input changed while opening")
	}
	return f, nil
}
func verifyPath(ctx context.Context, name string, file onboarding.File) error {
	f, err := openRegular(name, file.SizeBytes)
	if err != nil {
		return err
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.CopyBuffer(h, io.LimitReader(contextReader{ctx, f}, file.SizeBytes+1), make([]byte, 256<<10))
	if err != nil {
		return err
	}
	if n != file.SizeBytes || hex.EncodeToString(h.Sum(nil)) != file.SHA256 {
		return errChecksum
	}
	return nil
}
func cached(ctx context.Context, name string, file onboarding.File) (bool, error) {
	info, err := os.Lstat(name)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if err = noLinks(name); err != nil {
		return false, err
	}
	if !info.Mode().IsRegular() {
		return false, errors.New("public cache object must be regular")
	}
	if info.Size() == file.SizeBytes {
		err = verifyPath(ctx, name, file)
		if err == nil {
			return true, protectFile(name)
		}
		if !errors.Is(err, errChecksum) {
			return false, err
		}
	}
	if err = os.Remove(name); err != nil {
		return false, err
	}
	return false, nil
}
func copyVerified(ctx context.Context, source, destination string, file onboarding.File, executable bool) error {
	in, err := openRegular(source, file.SizeBytes)
	if err != nil {
		return err
	}
	defer in.Close()
	mode := os.FileMode(0600)
	if executable {
		mode = 0700
	}
	if _, err = os.Lstat(destination); err == nil {
		if err = checkRegular(destination, file.SizeBytes); err != nil {
			return err
		}
	} else if !os.IsNotExist(err) {
		return err
	}
	out, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	defer out.Close()
	if err = out.Chmod(mode); err != nil {
		return err
	}
	h := sha256.New()
	n, err := io.CopyBuffer(io.MultiWriter(out, h), io.LimitReader(contextReader{ctx, in}, file.SizeBytes+1), make([]byte, 256<<10))
	if err != nil {
		return err
	}
	if n != file.SizeBytes || hex.EncodeToString(h.Sum(nil)) != file.SHA256 {
		return errChecksum
	}
	return out.Sync()
}
