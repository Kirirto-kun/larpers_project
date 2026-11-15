package app

import (
	"campus/runner/internal/discovery"
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"sort"
)

var repositoryDigest = regexp.MustCompile(`^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$`)

// A local image ID is a configuration digest; a registry RepoDigest is a
// manifest digest. Docker's inspected association binds them without guessing
// that those different digests are equal or contacting a registry.
func resolveImageReference(ctx context.Context, image string, run discovery.Command) (string, error) {
	if len(image) <= 256 && repositoryDigest.MatchString(image) {
		return image, nil
	}
	if !regexp.MustCompile(`^sha256:[a-f0-9]{64}$`).MatchString(image) {
		return "", errors.New("participation requires a canonical repository@sha256 image digest")
	}
	raw, err := run(ctx, "docker", "image", "inspect", image)
	if err != nil {
		return "", errors.New("cannot inspect approved local image; prepare its cached repository digest")
	}
	var images []struct {
		ID          string   `json:"Id"`
		RepoDigests []string `json:"RepoDigests"`
	}
	if json.Unmarshal(raw, &images) != nil || len(images) != 1 || images[0].ID != image {
		return "", errors.New("local image inspection changed the approved image identity")
	}
	refs := []string{}
	for _, ref := range images[0].RepoDigests {
		if len(ref) <= 256 && repositoryDigest.MatchString(ref) {
			refs = append(refs, ref)
		}
	}
	if len(refs) == 0 {
		return "", errors.New("approved local image has no cached canonical RepoDigest; prepare a repository@sha256 profile before serving")
	}
	sort.Strings(refs)
	return refs[0], nil
}
