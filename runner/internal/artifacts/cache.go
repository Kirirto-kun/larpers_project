package artifacts

import (
	"campus/runner/internal/client"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

type flight struct {
	done     chan struct{}
	err      error
	reserved int64
}
type Cache struct {
	Root     string
	MaxBytes int64
	mu       sync.Mutex
	active   map[string]*flight
}

func NewCache(root string, maxBytes int64) *Cache {
	if maxBytes <= 0 {
		maxBytes = 2 << 30
	}
	return &Cache{Root: root, MaxBytes: maxBytes, active: map[string]*flight{}}
}

func (c *Cache) Materialize(ctx context.Context, api *client.Client, a client.Artifact, destination string) error {
	if !client.DigestPattern.MatchString(a.Digest) || a.SizeBytes < 0 {
		return errors.New("invalid cached artifact descriptor")
	}
	if a.SizeBytes > c.MaxBytes {
		return Download(ctx, api, a, destination)
	}
	c.mu.Lock()
	if previous := c.active[a.Digest]; previous != nil {
		c.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-previous.done:
			if previous.err != nil {
				return previous.err
			}
			return c.Materialize(ctx, api, a, destination)
		}
	}
	current := &flight{done: make(chan struct{})}
	c.active[a.Digest] = current
	c.mu.Unlock()
	var result error
	defer func() {
		c.mu.Lock()
		current.err = result
		delete(c.active, a.Digest)
		close(current.done)
		c.mu.Unlock()
	}()
	result = c.materialize(ctx, api, a, destination, current)
	return result
}

func (c *Cache) materialize(ctx context.Context, api *client.Client, a client.Artifact, destination string, current *flight) error {
	dir := filepath.Join(c.Root, "blobs")
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	if st, e := os.Lstat(dir); e != nil || !st.IsDir() || st.Mode()&os.ModeSymlink != 0 {
		return errors.New("cache must be a private regular directory")
	}
	blob := filepath.Join(dir, a.Digest)
	if st, e := os.Lstat(blob); e == nil {
		if !st.Mode().IsRegular() {
			return errors.New("special file in cache")
		}
		sum, n, e := HashFile(blob)
		if e == nil && sum == a.Digest && n == a.SizeBytes {
			now := time.Now()
			os.Chtimes(blob, now, now)
			return copyVerified(blob, destination, a)
		}
		if e = os.Remove(blob); e != nil {
			return e
		}
	}
	var partial int64
	if st, e := os.Lstat(blob + ".part"); e == nil {
		if !st.Mode().IsRegular() {
			return errors.New("special partial file in cache")
		}
		if st.Size() > a.SizeBytes {
			if e = os.Remove(blob + ".part"); e != nil {
				return e
			}
		} else {
			partial = st.Size()
		}
	}
	if e := c.reserve(a.Digest, max(0, a.SizeBytes-partial), current); e != nil {
		// A bounded cache is optional storage, never permission to exceed its quota.
		// Large/concurrent inputs may transfer straight to the private attempt tree.
		return Download(ctx, api, a, destination)
	}
	if e := Download(ctx, api, a, blob); e != nil {
		return e
	}
	return copyVerified(blob, destination, a)
}

func (c *Cache) reserve(digest string, additional int64, current *flight) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	entries, e := os.ReadDir(filepath.Join(c.Root, "blobs"))
	if e != nil {
		return e
	}
	type victim struct {
		path string
		size int64
		time time.Time
	}
	var candidates []victim
	var total int64
	for _, entry := range entries {
		key := strings.TrimSuffix(entry.Name(), ".part")
		if !client.DigestPattern.MatchString(key) {
			continue
		}
		st, e := entry.Info()
		if e != nil {
			return e
		}
		if !st.Mode().IsRegular() {
			return errors.New("special cache entry")
		}
		total += st.Size()
		if c.active[key] == nil {
			candidates = append(candidates, victim{filepath.Join(c.Root, "blobs", entry.Name()), st.Size(), st.ModTime()})
		}
	}
	for _, f := range c.active {
		total += f.reserved
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].time.Before(candidates[j].time) })
	for _, v := range candidates {
		if total+additional <= c.MaxBytes {
			break
		}
		if e := os.Remove(v.path); e == nil {
			total -= v.size
		}
	}
	if total+additional > c.MaxBytes {
		return errors.New("verified cache storage budget exhausted")
	}
	current.reserved = additional
	return nil
}

func copyVerified(source, destination string, a client.Artifact) error {
	if e := os.MkdirAll(filepath.Dir(destination), 0700); e != nil {
		return e
	}
	if _, e := os.Lstat(destination); e == nil {
		sum, n, e := HashFile(destination)
		if e == nil && sum == a.Digest && n == a.SizeBytes {
			return nil
		}
		return errors.New("artifact destination already exists")
	}
	in, e := os.Open(source)
	if e != nil {
		return e
	}
	defer in.Close()
	out, e := os.CreateTemp(filepath.Dir(destination), ".campus-blob-*")
	if e != nil {
		return e
	}
	name := out.Name()
	defer os.Remove(name)
	h := sha256.New()
	n, e := io.Copy(io.MultiWriter(out, h), io.LimitReader(in, a.SizeBytes+1))
	if e == nil {
		e = out.Sync()
	}
	closeErr := out.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	if n != a.SizeBytes || hex.EncodeToString(h.Sum(nil)) != a.Digest {
		return errors.New("cached artifact changed during copy")
	}
	return os.Rename(name, destination)
}
