package installer

import (
	"errors"
	"os"
	"path/filepath"
)

func noLinks(name string) error {
	name, err := filepath.Abs(name)
	if err != nil {
		return err
	}
	for {
		info, err := os.Lstat(name)
		if err != nil {
			return err
		}
		if linked(info) {
			return errors.New("public cache/input paths must not contain links or reparse points")
		}
		parent := filepath.Dir(name)
		if parent == name {
			return nil
		}
		name = parent
	}
}

func privateDir(name string) error {
	name, err := filepath.Abs(name)
	if err != nil {
		return err
	}
	if info, err := os.Lstat(name); err == nil {
		if !info.IsDir() {
			return errors.New("public cache path must be a directory")
		}
		if err = noLinks(name); err != nil {
			return err
		}
		return protect(name)
	} else if !os.IsNotExist(err) {
		return err
	}
	parent := filepath.Dir(name)
	if parent == name {
		return errors.New("public cache directory is unavailable")
	}
	if _, err = os.Lstat(parent); os.IsNotExist(err) {
		if err = privateDir(parent); err != nil {
			return err
		}
	} else if err != nil {
		return err
	}
	if err = noLinks(parent); err != nil {
		return err
	}
	if err = os.Mkdir(name, 0700); err != nil && !os.IsExist(err) {
		return err
	}
	if err = noLinks(name); err != nil {
		return err
	}
	info, err := os.Lstat(name)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return errors.New("public cache directory changed type")
	}
	return protect(name)
}
