package artifacts

import (
	"bytes"
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func HashFile(path string) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() {
		return "", 0, errors.New("artifact is not a regular file")
	}
	h := sha256.New()
	n, err := io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil)), n, err
}

func Download(ctx context.Context, c *client.Client, a client.Artifact, dest string) error {
	if !client.IDPattern.MatchString(a.ArtifactID) || !client.DigestPattern.MatchString(a.Digest) || a.SizeBytes < 0 {
		return errors.New("invalid artifact descriptor")
	}
	if err := os.MkdirAll(filepath.Dir(dest), 0700); err != nil {
		return err
	}
	if _, err := os.Lstat(dest); err == nil {
		h, n, e := HashFile(dest)
		if e == nil && h == a.Digest && n == a.SizeBytes {
			return nil
		}
		return errors.New("destination exists with a different digest")
	}
	part := dest + ".part"
	if st, e := os.Lstat(part); e == nil {
		if !st.Mode().IsRegular() {
			return errors.New("partial artifact must be a regular file")
		}
		if st.Size() > a.SizeBytes {
			if e := os.Truncate(part, 0); e != nil {
				return e
			}
		}
	}
	for attempt := 0; attempt < max(1, c.Attempts); attempt++ {
		var offset int64
		if st, e := os.Stat(part); e == nil {
			offset = st.Size()
		}
		if offset == a.SizeBytes {
			break
		}
		req, e := c.Request(ctx, "GET", "/artifacts/"+a.ArtifactID+"/content", nil, "")
		if e != nil {
			return e
		}
		if offset > 0 {
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-", offset))
		}
		resp, e := c.HTTP.Do(req)
		if e != nil {
			if attempt+1 >= c.Attempts {
				return e
			}
			if e = c.Wait(ctx, attempt); e != nil {
				return e
			}
			continue
		}
		if resp.StatusCode != 200 && resp.StatusCode != 206 {
			resp.Body.Close()
			if resp.StatusCode == 401 || resp.StatusCode == 403 {
				return &client.APIError{Status: resp.StatusCode, Code: "FORBIDDEN", Message: "artifact access denied"}
			}
			if resp.StatusCode >= 500 && attempt+1 < c.Attempts {
				if err := c.Wait(ctx, attempt); err != nil {
					return err
				}
				continue
			}
			return fmt.Errorf("artifact download HTTP %d", resp.StatusCode)
		}
		flags := os.O_WRONLY | os.O_CREATE
		if resp.StatusCode == 200 {
			offset = 0
			flags |= os.O_TRUNC
		} else {
			expected := fmt.Sprintf("bytes %d-%d/%d", offset, a.SizeBytes-1, a.SizeBytes)
			if resp.Header.Get("Content-Range") != expected {
				resp.Body.Close()
				return errors.New("invalid Content-Range")
			}
			flags |= os.O_APPEND
		}
		f, e := os.OpenFile(part, flags, 0600)
		if e != nil {
			resp.Body.Close()
			return e
		}
		n, e := io.Copy(f, io.LimitReader(resp.Body, a.SizeBytes-offset+1))
		resp.Body.Close()
		syncErr := f.Sync()
		f.Close()
		if n > a.SizeBytes-offset {
			return errors.New("download exceeds declared size")
		}
		if e == nil && syncErr != nil {
			e = syncErr
		}
		if e == nil && n+offset == a.SizeBytes {
			break
		}
		if attempt+1 >= c.Attempts {
			return errors.New("download interrupted before declared size")
		}
		if e = c.Wait(ctx, attempt); e != nil {
			return e
		}
	}
	// Empty artifacts still need a real local file before digest verification.
	if a.SizeBytes == 0 {
		f, e := os.OpenFile(part, os.O_CREATE|os.O_WRONLY, 0600)
		if e != nil {
			return e
		}
		f.Close()
	}
	h, n, e := HashFile(part)
	if e != nil {
		return e
	}
	if h != a.Digest || n != a.SizeBytes {
		os.Remove(part)
		return errors.New("artifact checksum/size mismatch")
	}
	return os.Rename(part, dest)
}

func Upload(ctx context.Context, c *client.Client, path string, fence client.Fence) (client.Artifact, error) {
	kind := "input"
	if fence.AttemptID != "" {
		kind = "output"
	}
	return UploadAs(ctx, c, path, fence, kind)
}

// UploadAs selects the foundation artifact purpose independently of its local name.
func UploadAs(ctx context.Context, c *client.Client, path string, fence client.Fence, kind string) (client.Artifact, error) {
	var result client.Artifact
	switch kind {
	case "input", "project", "output", "checkpoint":
	default:
		return result, errors.New("invalid upload kind")
	}
	partSize := c.UploadPartSize
	if partSize == 0 {
		partSize = 4 << 20
	}
	if partSize < 1 || partSize > 8<<20 {
		return result, errors.New("unsupported upload part size")
	}
	digest, size, err := HashFile(path)
	if err != nil {
		return result, err
	}
	name := filepath.Base(path)
	mediaType := "application/octet-stream"
	body := generated.UploadCreate{Name: name, SHA256: digest, SizeBytes: size, PartSizeBytes: &partSize, Kind: &kind, MediaType: &mediaType}
	if fence.AttemptID != "" || fence.Generation != 0 || fence.LeaseToken != "" {
		if !client.IDPattern.MatchString(fence.AttemptID) || fence.Generation < 1 || strings.TrimSpace(fence.LeaseToken) == "" {
			return result, errors.New("invalid upload attempt proof")
		}
		body.AttemptID = &fence.AttemptID
		body.Proof = &generated.AttemptProof{Generation: fence.Generation, LeaseToken: fence.LeaseToken}
	}
	var state generated.Upload
	// The foundation does not promise idempotency for creation. An unknown
	// acknowledgement must reach the caller rather than creating another upload.
	if err = c.DoOnce(ctx, "POST", "/uploads", body, &state, ""); err != nil {
		return result, err
	}
	if !client.IDPattern.MatchString(state.ID) {
		return result, errors.New("invalid upload ID")
	}
	uploadID := state.ID
	partCount := size / partSize
	if size%partSize != 0 {
		partCount++
	}
	if err = validateUpload(state, uploadID, partCount); err != nil {
		return result, err
	}
	route := "/uploads/" + uploadID
	// A status response must stand on its own; omitted fields cannot inherit
	// their earlier values from the create acknowledgement.
	state = generated.Upload{}
	if err = c.Do(ctx, "GET", route, nil, &state, ""); err != nil {
		return result, err
	}
	if err = validateUpload(state, uploadID, partCount); err != nil {
		return result, err
	}
	f, err := os.Open(path)
	if err != nil {
		return result, err
	}
	defer f.Close()
	done := map[int64]bool{}
	// The server's inventory is only reusable when it describes these local
	// bytes. Check every retained part before sending any further mutations.
	for _, p := range state.Parts {
		if p.Number < 0 || p.Number >= partCount || done[p.Number] {
			return result, errors.New("invalid retained upload part number")
		}
		offset := p.Number * partSize
		if p.SizeBytes != min(partSize, size-offset) {
			return result, errors.New("retained upload part size mismatch")
		}
		buf := make([]byte, p.SizeBytes)
		if _, err = f.ReadAt(buf, offset); err != nil {
			return result, err
		}
		sum := sha256.Sum256(buf)
		if p.SHA256 != hex.EncodeToString(sum[:]) {
			return result, errors.New("retained upload part checksum mismatch")
		}
		done[p.Number] = true
	}
	if state.State == "ready" {
		if err = unchangedArtifact(path, digest, size); err != nil {
			return result, err
		}
		return uploadedArtifact(state.Artifact, digest, size, name, kind)
	}
	for part, offset := int64(0), int64(0); offset < size; part, offset = part+1, offset+partSize {
		if done[part] {
			continue
		}
		buf := make([]byte, min(partSize, size-offset))
		if _, err = f.ReadAt(buf, offset); err != nil {
			return result, err
		}
		sum := sha256.Sum256(buf)
		if err = putUploadPart(ctx, c, route, part, buf, hex.EncodeToString(sum[:])); err != nil {
			return result, err
		}
	}
	// Detect local mutation between checksum and upload/finalize.
	if err = unchangedArtifact(path, digest, size); err != nil {
		return result, err
	}
	var finalized generated.Artifact
	if err = c.DoOnce(ctx, "POST", route+"/finalize", nil, &finalized, ""); err != nil {
		var api *client.APIError
		if errors.As(err, &api) && api.Status < 500 && api.Status != 429 {
			return result, err
		}
		// Finalization may have committed despite a lost response. Its published
		// status resource can prove completion without replaying the mutation.
		var status generated.Upload
		if statusErr := c.Do(ctx, "GET", route, nil, &status, ""); statusErr != nil {
			return result, errors.Join(err, statusErr)
		}
		if statusErr := validateUpload(status, uploadID, partCount); statusErr != nil {
			return result, errors.Join(err, statusErr)
		}
		if status.State != "ready" {
			return result, fmt.Errorf("upload finalization acknowledgement unknown: %w", err)
		}
		return uploadedArtifact(status.Artifact, digest, size, name, kind)
	}
	return uploadedArtifact(&finalized, digest, size, name, kind)
}

func validateUpload(state generated.Upload, id string, parts int64) error {
	if state.ID != id || state.PartCount != parts {
		return errors.New("upload identity/part count mismatch")
	}
	if state.State != "pending" && state.State != "ready" {
		return errors.New("upload is not pending or ready")
	}
	return nil
}

func unchangedArtifact(path, digest string, size int64) error {
	current, n, err := HashFile(path)
	if err != nil || current != digest || n != size {
		return errors.New("artifact changed during upload")
	}
	return nil
}

func uploadedArtifact(a *generated.Artifact, digest string, size int64, name, kind string) (client.Artifact, error) {
	if a == nil || !client.IDPattern.MatchString(a.ID) || a.SHA256 != digest || a.SizeBytes != size || a.Name != name || strings.TrimSpace(a.MediaType) == "" {
		return client.Artifact{}, errors.New("finalized artifact descriptor/checksum/size mismatch")
	}
	return client.Artifact{ArtifactID: a.ID, Digest: a.SHA256, SizeBytes: a.SizeBytes, Path: name, Kind: kind, MediaType: a.MediaType}, nil
}

func putUploadPart(ctx context.Context, c *client.Client, route string, part int64, buf []byte, digest string) error {
	var last error
	for attempt := 0; attempt < max(1, c.Attempts); attempt++ {
		req, err := c.Request(ctx, "PUT", route+"/parts/"+strconv.FormatInt(part, 10), bytes.NewReader(buf), "")
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/octet-stream")
		req.Header.Set("X-Content-SHA256", digest)
		resp, err := c.HTTP.Do(req)
		if err == nil {
			raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 65537))
			resp.Body.Close()
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				if readErr == nil {
					var ack generated.UploadPart
					if len(raw) > 65536 || json.Unmarshal(raw, &ack) != nil || ack.Number != part || ack.SHA256 != digest || ack.SizeBytes != int64(len(buf)) {
						return errors.New("upload part acknowledgement mismatch")
					}
					return nil
				}
				err = readErr
			} else {
				api := &client.APIError{Status: resp.StatusCode, Code: http.StatusText(resp.StatusCode), Message: "upload part rejected"}
				if readErr == nil && len(raw) <= 65536 {
					json.Unmarshal(raw, api)
				}
				err = api
				if client.Terminal(api) || resp.StatusCode < 500 && resp.StatusCode != 429 {
					return err
				}
			}
		}
		last = err
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if attempt+1 < c.Attempts {
			if err = c.Wait(ctx, attempt); err != nil {
				return err
			}
		}
	}
	return last
}
