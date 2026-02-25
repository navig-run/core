package profilemgr

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type Lock struct {
	mu     sync.Mutex
	dir    string
	locked bool
	file   *os.File
}

func NewLock(dir string) *Lock {
	return &Lock{
		dir: dir,
	}
}

func (l *Lock) Acquire() error {
	l.mu.Lock()
	defer l.mu.Unlock()

	if l.locked {
		return fmt.Errorf("PROFILE_IN_USE")
	}

	if err := os.MkdirAll(l.dir, 0700); err != nil {
		return fmt.Errorf("failed to create target directory: %w", err)
	}

	lockFile := filepath.Join(l.dir, ".lock")

	f, err := os.OpenFile(lockFile, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		if os.IsExist(err) {
			return fmt.Errorf("PROFILE_IN_USE")
		}
		return fmt.Errorf("failed to create lock file: %w", err)
	}

	l.file = f
	l.locked = true
	return nil
}

func (l *Lock) AcquireWithTimeout(timeout time.Duration, retryInterval time.Duration) error {
	deadline := time.Now().Add(timeout)
	for {
		err := l.Acquire()
		if err == nil {
			return nil
		}
		if err.Error() != "PROFILE_IN_USE" {
			return err
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("timeout acquiring lock: %w", err)
		}
		time.Sleep(retryInterval)
	}
}

func (l *Lock) Release() error {
	l.mu.Lock()
	defer l.mu.Unlock()

	if !l.locked {
		return nil
	}

	if l.file != nil {
		l.file.Close()
		l.file = nil
	}

	lockFile := filepath.Join(l.dir, ".lock")
	if err := os.Remove(lockFile); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to remove lock file: %w", err)
	}

	l.locked = false
	return nil
}

// RWMutex encapsulates Go routine synchronization specifically for the Registry manager.
type RWMutex struct {
	mu sync.RWMutex
}

func NewRWMutex() *RWMutex {
	return &RWMutex{}
}

func (m *RWMutex) RLock()   { m.mu.RLock() }
func (m *RWMutex) RUnlock() { m.mu.RUnlock() }
func (m *RWMutex) Lock()    { m.mu.Lock() }
func (m *RWMutex) Unlock()  { m.mu.Unlock() }

// Global registry directory coordination
type RegistryLock struct {
	Lock *Lock
}

func NewRegistryLock(regDir string) *RegistryLock {
	return &RegistryLock{
		Lock: NewLock(regDir),
	}
}
