package artifacts

import (
	"bytes"
	"campus/runner/internal/client"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
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

type uploadState struct {
	UploadID  string `json:"upload_id"`
	PartSize  int64  `json:"part_size_bytes"`
	Completed []int  `json:"completed_parts"`
}

func Upload(ctx context.Context, c *client.Client, path string, fence client.Fence) (client.Artifact, error) {
	var result client.Artifact
	digest, size, err := HashFile(path)
	if err != nil {
		return result, err
	}
	body := map[string]any{"digest": digest, "size_bytes": size, "attempt_id": fence.AttemptID, "generation": fence.Generation, "lease_token": fence.LeaseToken}
	var state uploadState
	key := "upload:" + fence.AttemptID + ":" + strconv.FormatInt(fence.Generation, 10) + ":" + digest
	if err = c.Do(ctx, "POST", "/uploads", body, &state, key); err != nil {
		return result, err
	}
	if !client.IDPattern.MatchString(state.UploadID) {
		return result, errors.New("invalid upload ID")
	}
	route := "/uploads/" + state.UploadID
	if err = c.Do(ctx, "GET", route, nil, &state, ""); err != nil {
		return result, err
	}
	if state.PartSize <= 0 || state.PartSize > 16<<20 {
		return result, errors.New("unsupported upload part size")
	}
	done := map[int]bool{}
	for _, p := range state.Completed {
		done[p] = true
	}
	f, err := os.Open(path)
	if err != nil {
		return result, err
	}
	defer f.Close()
	for part, offset := 0, int64(0); offset < size; part, offset = part+1, offset+state.PartSize {
		if done[part] {
			continue
		}
		buf := make([]byte, min(state.PartSize, size-offset))
		if _, err = f.ReadAt(buf, offset); err != nil {
			return result, err
		}
		sum := sha256.Sum256(buf)
		for attempt := 0; attempt < max(1, c.Attempts); attempt++ {
			req, e := c.Request(ctx, "PUT", route+"/parts/"+strconv.Itoa(part), bytes.NewReader(buf), key+":"+strconv.Itoa(part))
			if e != nil {
				return result, e
			}
			req.Header.Set("Content-Type", "application/octet-stream")
			req.Header.Set("X-Content-SHA256", hex.EncodeToString(sum[:]))
			req.Header.Set("X-Attempt-ID", fence.AttemptID)
			req.Header.Set("X-Generation", strconv.FormatInt(fence.Generation, 10))
			req.Header.Set("X-Lease-Token", fence.LeaseToken)
			resp, e := c.HTTP.Do(req)
			if e == nil {
				io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
				resp.Body.Close()
				if resp.StatusCode >= 200 && resp.StatusCode < 300 {
					err = nil
					break
				}
				if resp.StatusCode == 401 || resp.StatusCode == 403 || resp.StatusCode == 409 || resp.StatusCode == 410 {
					return result, &client.APIError{Status: resp.StatusCode, Code: "STALE_ATTEMPT", Message: "upload rejected"}
				}
				e = fmt.Errorf("upload part HTTP %d", resp.StatusCode)
				if resp.StatusCode < 500 && resp.StatusCode != 429 {
					return result, e
				}
			}
			err = e
			if attempt+1 < c.Attempts {
				if e = c.Wait(ctx, attempt); e != nil {
					return result, e
				}
			}
		}
		if err != nil {
			return result, err
		}
	}
	// Detect local mutation between checksum and upload/finalize.
	current, n, e := HashFile(path)
	if e != nil || current != digest || n != size {
		return result, errors.New("artifact changed during upload")
	}
	if err = c.Do(ctx, "POST", route+"/finalize", body, &result, key+":finalize"); err != nil {
		return result, err
	}
	if !client.IDPattern.MatchString(result.ArtifactID) || result.Digest != digest || result.SizeBytes != size {
		return result, errors.New("finalized artifact checksum/size mismatch")
	}
	result.Path = filepath.ToSlash(filepath.Base(path))
	result.Kind = strings.TrimSpace(result.Kind)
	return result, nil
}
