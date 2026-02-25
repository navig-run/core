package profilemgr

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"

	"navig-core/host/internal/browser"
)

// ProfileLock represents a lock for a profile directory.
type ProfileLock struct {
	mu   sync.Mutex
	path string
	file *os.File
}

// NewProfileLock creates a new ProfileLock instance.
func NewProfileLock(path string) *ProfileLock {
	return &ProfileLock{
		path: path,
	}
}

// Acquire attempts to acquire the lock for the profile.
// It creates a lock file and holds an exclusive lock on it.
func (l *ProfileLock) Acquire() error {
	l.mu.Lock()
	defer l.mu.Unlock()

	if l.file != nil {
		return fmt.Errorf("lock already acquired for %s", l.path)
	}

	lockFilePath := filepath.Join(l.path, "lock")
	file, err := os.OpenFile(lockFilePath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		if os.IsExist(err) {
			return fmt.Errorf("profile is already locked by another process: %w", err)
		}
		return fmt.Errorf("failed to create lock file: %w", err)
	}
	l.file = file
	return nil
}

// Release releases the acquired lock.
// It closes and removes the lock file.
func (l *ProfileLock) Release() error {
	l.mu.Lock()
	defer l.mu.Unlock()

	if l.file == nil {
		return fmt.Errorf("lock not acquired for %s", l.path)
	}

	if err := l.file.Close(); err != nil {
		return fmt.Errorf("failed to close lock file: %w", err)
	}

	lockFilePath := filepath.Join(l.path, "lock")
	if err := os.Remove(lockFilePath); err != nil {
		return fmt.Errorf("failed to remove lock file: %w", err)
	}

	l.file = nil
	return nil
}

type ProfileManager struct{}

func New() *ProfileManager {
	return &ProfileManager{}
}

func (m *ProfileManager) Ensure(profileName browser.ProfileName) (*browser.ProfileInfo, error) {
	dir, err := browser.ResolveProfileDir(profileName)
	if err != nil {
		return nil, err
	}

	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, fmt.Errorf("failed to create profile dir: %w", err)
	}

	// For basic "file-lock", we can just ensure a lock file exists,
	// Full advisory locking can be added using syscalls if required.
	lockFile := filepath.Join(dir, "lockfile")
	if _, err := os.Stat(lockFile); os.IsNotExist(err) {
		f, err := os.Create(lockFile)
		if err == nil {
			f.Close()
		}
	}

	return &browser.ProfileInfo{
		Name:    profileName,
		Path:    dir,
		Browser: browser.BrowserChrome, // Default
	}, nil
}

// CloneProfile performs a fast physical copy of the profile, effectively creating a Snapshot
// suitable for simultaneous read-write usage without physical lock collision on sqlite datastores.
func CloneProfile(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)

		// Skip lock files
		if info.Name() == "lock" || info.Name() == "lockfile" {
			return nil
		}

		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}

		srcF, err := os.Open(path)
		if err != nil {
			// Skip files locked or unreadable by other browser processes
			return nil
		}
		defer srcF.Close()

		dstF, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, info.Mode())
		if err != nil {
			return nil // Skip if we cannot write
		}
		defer dstF.Close()

		io.Copy(dstF, srcF)
		return nil
	})
}
