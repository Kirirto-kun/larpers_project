package session

import (
	"errors"
	"os"
)

// OpenSharedRead opens an existing regular state file read-only without following
// its final symlink. Its handle permits an atomic replacement while it is read,
// including on Windows bind mounts shared with the protected guard.
func OpenSharedRead(path string) (*os.File, error) {
	file, err := openSharedRead(path)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, err
	}
	if !info.Mode().IsRegular() {
		file.Close()
		return nil, &os.PathError{Op: "open", Path: path, Err: errors.New("state file must be a regular file")}
	}
	return file, nil
}
