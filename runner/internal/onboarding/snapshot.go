package onboarding

import (
	"campus/runner/internal/session"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
)

// SnapshotMetadata freezes the verified profiles, CA and optional invitation.
// Long image loads/probes must never reopen mutable download-folder metadata.
func (b *Bundle) SnapshotMetadata(ctx context.Context) (string, func(), error) {
	dir, err := session.PrivateTempDir("", "campus-connect-")
	if err != nil {
		return "", nil, err
	}
	cleanup := func() { os.RemoveAll(dir) }
	files := []File{b.Manifest.Profiles}
	if b.Manifest.CAFile != nil {
		files = append(files, *b.Manifest.CAFile)
	}
	if b.Manifest.InviteFile != nil {
		files = append(files, *b.Manifest.InviteFile)
	}
	for _, file := range files {
		f, e := regular(b.Path(file), 2<<20)
		if e != nil {
			cleanup()
			return "", nil, e
		}
		raw, e := io.ReadAll(io.LimitReader(contextReader{ctx, f}, (2<<20)+1))
		f.Close()
		sum := sha256.Sum256(raw)
		if e != nil || int64(len(raw)) != file.SizeBytes || hex.EncodeToString(sum[:]) != file.SHA256 {
			cleanup()
			return "", nil, errors.New("connection metadata changed before private staging")
		}
		path := filepath.Join(dir, filepath.FromSlash(file.Path))
		if e = os.MkdirAll(filepath.Dir(path), 0700); e != nil {
			cleanup()
			return "", nil, e
		}
		if e = os.WriteFile(path, raw, 0600); e != nil {
			cleanup()
			return "", nil, e
		}
	}
	return dir, cleanup, nil
}
