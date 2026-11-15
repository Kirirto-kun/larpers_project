// Package installer prepares pinned public release downloads for local onboarding.
package installer

import (
	"bytes"
	"campus/runner/internal/client"
	"campus/runner/internal/onboarding"
	"campus/runner/internal/packaging"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

type Catalog struct {
	SchemaVersion string           `json:"schema_version"`
	Endpoint      string           `json:"endpoint"`
	CAFile        *onboarding.File `json:"ca_file,omitempty"`
	Targets       []Target         `json:"targets"`
	raw           []byte
	url           string
	digest        string
}

type Target struct {
	OS       string             `json:"os"`
	Arch     string             `json:"arch"`
	Runner   onboarding.File    `json:"runner"`
	Profiles onboarding.File    `json:"profiles"`
	Images   []onboarding.Image `json:"images"`
}

const catalogLimit = 64 << 10

func releaseURL(value string) (*url.URL, error) {
	u, err := url.Parse(value)
	if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || u.Opaque != "" || u.RawPath != "" || strings.ContainsAny(value, "\\%\r\n") {
		return nil, errors.New("invalid public release URL")
	}
	if u.Scheme != "https" && (u.Scheme != "http" || u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1" && u.Hostname() != "::1") {
		return nil, errors.New("public release requires HTTPS")
	}
	if !strings.HasPrefix(u.Path, "/") || strings.HasSuffix(u.Path, "/") || safePath(strings.TrimPrefix(u.Path, "/")) != nil {
		return nil, errors.New("invalid public release catalog path")
	}
	return u, nil
}

func safePath(name string) error {
	if len(name) > 1024 || packaging.SafePath(name) != nil || strings.EqualFold(name, "connection.json") || strings.HasPrefix(strings.ToLower(name), "connection.json/") {
		return errors.New("invalid release file path")
	}
	for _, ch := range name {
		if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch >= '0' && ch <= '9' || strings.ContainsRune("._-/", ch)) {
			return errors.New("release paths must use canonical ASCII file names")
		}
	}
	for _, part := range strings.Split(name, "/") {
		if len(part) > 255 {
			return errors.New("release path component is too long")
		}
	}
	return nil
}

func downloadClient(h *http.Client) *http.Client {
	if h == nil {
		h = http.DefaultClient
	}
	copy := *h
	copy.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	copy.Jar = nil
	// A bounded request context owns the deadline; image downloads can be large.
	copy.Timeout = 0
	return &copy
}

func request(ctx context.Context, h *http.Client, location string, offset int64) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, location, nil)
	if err != nil {
		return nil, errors.New("invalid release request")
	}
	req.Header.Set("Accept-Encoding", "identity")
	if offset > 0 {
		req.Header.Set("Range", "bytes="+strconv.FormatInt(offset, 10)+"-")
	}
	resp, err := downloadClient(h).Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, errors.New("public release request failed")
	}
	if resp.Uncompressed || resp.Header.Get("Content-Encoding") != "" && resp.Header.Get("Content-Encoding") != "identity" {
		resp.Body.Close()
		return nil, errors.New("encoded release responses are forbidden")
	}
	return resp, nil
}

// FetchCatalog verifies the trusted digest before decoding any public metadata.
func FetchCatalog(ctx context.Context, h *http.Client, catalogURL, expectedSHA string) (*Catalog, error) {
	if _, err := releaseURL(catalogURL); err != nil {
		return nil, err
	}
	if !client.DigestPattern.MatchString(expectedSHA) {
		return nil, errors.New("catalog needs its trusted SHA256")
	}
	ctx, cancel := context.WithTimeout(ctx, time.Minute)
	defer cancel()
	resp, err := request(ctx, h, catalogURL, 0)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK || resp.ContentLength > catalogLimit {
		return nil, errors.New("public catalog response rejected")
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, catalogLimit+1))
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if err != nil || len(raw) > catalogLimit {
		return nil, errors.New("public catalog exceeds its limit or was interrupted")
	}
	sum := sha256.Sum256(raw)
	if hex.EncodeToString(sum[:]) != expectedSHA {
		return nil, errors.New("public catalog checksum mismatch")
	}
	c, err := decodeCatalog(raw)
	if err != nil {
		return nil, err
	}
	c.raw, c.url, c.digest = append([]byte(nil), raw...), catalogURL, expectedSHA
	return c, nil
}

func decodeCatalog(raw []byte) (*Catalog, error) {
	var c Catalog
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if d.Decode(&c) != nil {
		return nil, errors.New("invalid public catalog JSON")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return nil, errors.New("public catalog has trailing data")
	}
	if c.SchemaVersion != "1" || len(c.Targets) < 1 || len(c.Targets) > 6 {
		return nil, errors.New("invalid public catalog schema or targets")
	}
	if _, err := client.New(c.Endpoint, "", "", nil); err != nil {
		return nil, errors.New("invalid public coordinator endpoint")
	}
	seen := map[string]bool{}
	for _, target := range c.Targets {
		key := target.OS + "/" + target.Arch
		if seen[key] || target.OS != "windows" && target.OS != "darwin" && target.OS != "linux" || target.Arch != "amd64" && target.Arch != "arm64" {
			return nil, errors.New("invalid or duplicate public target")
		}
		seen[key] = true
		files := []onboarding.File{target.Runner, target.Profiles}
		limits := []int64{128 << 20, 2 << 20}
		if c.CAFile != nil {
			files = append(files, *c.CAFile)
			limits = append(limits, 2<<20)
		}
		if len(target.Images) < 1 || len(target.Images) > 2 {
			return nil, errors.New("public target needs CPU and at most one CUDA image")
		}
		accelerators := map[string]bool{}
		for _, image := range target.Images {
			if accelerators[image.Accelerator] || image.Accelerator != "cpu" && image.Accelerator != "cuda" || image.Platform != "linux/"+target.Arch || !client.ImagePattern.MatchString(image.ImageDigest) || image.Accelerator == "cuda" && (target.Arch != "amd64" || target.OS == "darwin") {
				return nil, errors.New("invalid public target runtime image")
			}
			accelerators[image.Accelerator] = true
			files = append(files, image.File)
			limits = append(limits, 8<<30)
		}
		if !accelerators["cpu"] {
			return nil, errors.New("public target requires its CPU image")
		}
		var total int64
		for i, file := range files {
			if safePath(file.Path) != nil || !client.DigestPattern.MatchString(file.SHA256) || file.SizeBytes < 1 || file.SizeBytes > limits[i] {
				return nil, errors.New("invalid public file path, checksum, or quota")
			}
			total += file.SizeBytes
			if total > 16<<30 {
				return nil, errors.New("public target exceeds total quota")
			}
			for _, prior := range files[:i] {
				a, b := strings.ToLower(prior.Path), strings.ToLower(file.Path)
				if a == b || strings.HasPrefix(a, b+"/") || strings.HasPrefix(b, a+"/") {
					return nil, errors.New("public file paths collide")
				}
			}
		}
	}
	return &c, nil
}

// Select returns a detached copy so callers cannot mutate frozen release data.
func (c *Catalog) Select(goos, arch string) (Target, error) {
	if c == nil {
		return Target{}, errors.New("public catalog is required")
	}
	for _, t := range c.Targets {
		if t.OS == goos && t.Arch == arch {
			t.Images = append([]onboarding.Image(nil), t.Images...)
			return t, nil
		}
	}
	return Target{}, errors.New("public release is unavailable for this operating system/architecture")
}
