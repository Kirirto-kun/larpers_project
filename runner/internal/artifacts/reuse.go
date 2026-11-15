package artifacts

import (
	"campus/runner/internal/client"
	"context"
	"errors"
	"fmt"
)

// ReuseOwned asks the authenticated coordinator to prove ownership for these
// exact bytes. No local artifact ID is trusted across accounts or endpoints.
func ReuseOwned(ctx context.Context, c *client.Client, path, kind string) (client.Artifact, bool, error) {
	var result client.Artifact
	if kind != "input" && kind != "project" {
		return result, false, errors.New("only member input/project artifacts can be reused")
	}
	digest, size, err := HashFile(path)
	if err != nil {
		return result, false, err
	}
	route := fmt.Sprintf("/artifacts/lookup?sha256=%s&size_bytes=%d&kind=%s", digest, size, kind)
	if err = c.Do(ctx, "GET", route, nil, &result, ""); err != nil {
		var api *client.APIError
		if errors.As(err, &api) && api.Status == 404 {
			return client.Artifact{}, false, nil
		}
		return client.Artifact{}, false, err
	}
	if !client.IDPattern.MatchString(result.ArtifactID) || result.Digest != digest || result.SizeBytes != size || result.MediaType == "" {
		return client.Artifact{}, false, errors.New("owned artifact lookup returned mismatched content metadata")
	}
	if err = unchangedArtifact(path, digest, size); err != nil {
		return client.Artifact{}, false, err
	}
	result.Kind = kind
	return result, true, nil
}

func UploadOwned(ctx context.Context, c *client.Client, path, kind string) (client.Artifact, bool, error) {
	artifact, reused, err := ReuseOwned(ctx, c, path, kind)
	if err != nil || reused {
		return artifact, reused, err
	}
	artifact, err = UploadAs(ctx, c, path, client.Fence{}, kind)
	return artifact, false, err
}
