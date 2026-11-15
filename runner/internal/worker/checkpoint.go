package worker

import (
	"campus/runner/internal/client"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

var errCheckpointPublicationUnknown = errors.New("checkpoint publication acknowledgement unknown; v1 cannot reconcile its pointer")

func checkpointCompatibility(root string) (string, error) {
	read := func(name string, limit int64) ([]byte, error) {
		path := filepath.Join(root, name)
		st, err := os.Lstat(path)
		if err != nil {
			return nil, err
		}
		if !st.Mode().IsRegular() || st.Size() > limit {
			return nil, errors.New("invalid checkpoint metadata type or size")
		}
		data, err := os.ReadFile(path)
		if int64(len(data)) > limit {
			return nil, errors.New("checkpoint metadata exceeds quota")
		}
		return data, err
	}
	raw, err := read("manifest.json", 8<<20)
	if err != nil {
		return "", err
	}
	ready, err := read("READY", 256)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	if strings.TrimSpace(string(ready)) != hex.EncodeToString(sum[:]) {
		return "", errors.New("checkpoint manifest checksum mismatch")
	}
	var manifest struct {
		SchemaVersion string            `json:"schema_version"`
		Binding       map[string]string `json:"binding"`
	}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return "", err
	}
	if manifest.SchemaVersion != "1" || len(manifest.Binding) != 4 {
		return "", errors.New("checkpoint requires exactly four compatibility bindings")
	}
	for _, key := range []string{"code_digest", "input_digest", "environment_digest", "trainer_digest"} {
		if !client.DigestPattern.MatchString(manifest.Binding[key]) {
			return "", errors.New("checkpoint compatibility binding must be SHA-256")
		}
	}
	// Matches sdk/python/campus/checkpoint.py canonical(_binding(binding)):
	// sorted ASCII keys/hex strings, compact JSON, no trailing newline. Foundation
	// publishes the digest field but has not specified its derivation yet.
	canonical, err := json.Marshal(manifest.Binding)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return hex.EncodeToString(digest[:]), nil
}
