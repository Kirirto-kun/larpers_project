package installer

import (
	"campus/runner/internal/onboarding"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
)

func downloadObject(ctx context.Context, h *http.Client, catalogURL, objects string, file onboarding.File) (string, error) {
	destination := filepath.Join(objects, file.SHA256)
	if ok, err := cached(ctx, destination, file); err != nil || ok {
		return destination, err
	}
	partial := destination + ".partial"
	var offset int64
	info, err := os.Lstat(partial)
	if err == nil {
		if err = noLinks(partial); err != nil {
			return "", err
		}
		if !info.Mode().IsRegular() {
			return "", errors.New("partial release download must be regular")
		}
		if err = protectFile(partial); err != nil {
			return "", err
		}
		offset = info.Size()
		if offset >= file.SizeBytes {
			if offset == file.SizeBytes && verifyPath(ctx, partial, file) == nil {
				if err = os.Rename(partial, destination); err != nil {
					return "", err
				}
				return destination, nil
			}
			if err = ctx.Err(); err != nil {
				return "", err
			}
			if err = os.Remove(partial); err != nil {
				return "", err
			}
			offset = 0
		}
	} else if !os.IsNotExist(err) {
		return "", err
	}
	base, err := releaseURL(catalogURL)
	if err != nil {
		return "", err
	}
	ref := &url.URL{Path: file.Path}
	location := base.ResolveReference(ref)
	if location.Scheme != base.Scheme || location.Host != base.Host {
		return "", errors.New("cross-origin public artifact rejected")
	}
	resp, err := request(ctx, h, location.String(), offset)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if len(resp.TransferEncoding) != 0 {
		return "", errors.New("public artifact needs an exact content length")
	}
	if resp.StatusCode == http.StatusOK {
		if resp.ContentLength != file.SizeBytes || len(resp.Header.Values("Content-Range")) != 0 {
			return "", errors.New("public artifact size/range mismatch")
		}
		offset = 0
	} else if resp.StatusCode == http.StatusPartialContent && offset > 0 {
		want := fmt.Sprintf("bytes %d-%d/%d", offset, file.SizeBytes-1, file.SizeBytes)
		if resp.ContentLength != file.SizeBytes-offset || len(resp.Header.Values("Content-Range")) != 1 || resp.Header.Get("Content-Range") != want {
			return "", errors.New("public artifact resume range mismatch")
		}
	} else {
		return "", errors.New("public artifact response rejected")
	}
	flags := os.O_WRONLY | os.O_CREATE
	if offset == 0 {
		flags |= os.O_TRUNC
	} else {
		flags |= os.O_APPEND
	}
	if _, err = os.Lstat(partial); err == nil {
		if err = checkRegular(partial, file.SizeBytes); err != nil {
			return "", err
		}
	} else if !os.IsNotExist(err) {
		return "", err
	}
	out, err := os.OpenFile(partial, flags, 0600)
	if err != nil {
		return "", err
	}
	n, copyErr := io.CopyBuffer(out, io.LimitReader(contextReader{ctx, resp.Body}, file.SizeBytes-offset+1), make([]byte, 256<<10))
	syncErr := out.Sync()
	closeErr := out.Close()
	if copyErr != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", errors.New("public artifact download interrupted; verified resume is available")
	}
	if syncErr != nil {
		return "", syncErr
	}
	if closeErr != nil {
		return "", closeErr
	}
	if n != file.SizeBytes-offset {
		if n > file.SizeBytes-offset {
			os.Remove(partial)
		}
		return "", errors.New("public artifact download has an incorrect length")
	}
	if err = verifyPath(ctx, partial, file); err != nil {
		if errors.Is(err, errChecksum) {
			os.Remove(partial)
		}
		return "", err
	}
	if err = os.Rename(partial, destination); err != nil {
		return "", err
	}
	return destination, nil
}
