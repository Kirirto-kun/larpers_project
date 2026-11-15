package installer

import (
	"campus/runner/internal/client"
	"campus/runner/internal/onboarding"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
)

// Keep raw approved fields (including dependency pins) while selecting exactly
// the runtime identities materialized in the local connection manifest.
func selectedProfiles(ctx context.Context, name string, file onboarding.File, all, selected []onboarding.Image) ([]byte, error) {
	if err := verifyPath(ctx, name, file); err != nil {
		return nil, err
	}
	f, err := openRegular(name, 2<<20)
	if err != nil {
		return nil, err
	}
	raw, err := io.ReadAll(io.LimitReader(contextReader{ctx, f}, (2<<20)+1))
	f.Close()
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(raw)
	if int64(len(raw)) != file.SizeBytes || hex.EncodeToString(sum[:]) != file.SHA256 {
		return nil, errChecksum
	}
	var doc map[string]json.RawMessage
	if json.Unmarshal(raw, &doc) != nil {
		return nil, errors.New("invalid approved public profiles")
	}
	var schema string
	var profiles []json.RawMessage
	if json.Unmarshal(doc["schema_version"], &schema) != nil || schema != "1" || json.Unmarshal(doc["profiles"], &profiles) != nil || len(profiles) != len(all) {
		return nil, errors.New("public profiles must describe exactly the target runtimes")
	}
	want := map[string]bool{}
	for _, image := range selected {
		want[image.Accelerator] = true
	}
	seen := map[string]bool{}
	ids := map[string]bool{}
	filtered := []json.RawMessage{}
	for _, rawProfile := range profiles {
		var p struct {
			ID           string `json:"id"`
			Accelerator  string `json:"accelerator"`
			Platform     string `json:"platform"`
			ImageDigest  string `json:"image_digest"`
			GuardVersion string `json:"guard_version"`
		}
		if json.Unmarshal(rawProfile, &p) != nil || !client.IDPattern.MatchString(p.ID) || ids[p.ID] || p.GuardVersion != "1" || seen[p.Accelerator] {
			return nil, errors.New("invalid approved public profile identity")
		}
		ids[p.ID] = true
		matched := false
		for _, image := range all {
			if p.Accelerator == image.Accelerator && p.Platform == image.Platform && p.ImageDigest == image.ImageDigest {
				matched = true
			}
		}
		if !matched {
			return nil, errors.New("public profile differs from the approved target image")
		}
		seen[p.Accelerator] = true
		if want[p.Accelerator] {
			filtered = append(filtered, rawProfile)
		}
	}
	if len(filtered) != len(selected) {
		return nil, errors.New("selected public runtime profile is missing")
	}
	doc["profiles"], err = json.Marshal(filtered)
	if err != nil {
		return nil, err
	}
	return json.Marshal(doc)
}
