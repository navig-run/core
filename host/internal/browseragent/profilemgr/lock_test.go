package profilemgr_test

import (
	"os"
	"path/filepath"
	"testing"

	"navig-core/host/internal/browseragent/profilemgr"
)

func TestLockAcquireRelease(t *testing.T) {
	dir := filepath.Join(os.TempDir(), "navig_lock_test")
	defer os.RemoveAll(dir)

	lock := profilemgr.NewLock(dir)

	if err := lock.Acquire(); err != nil {
		t.Fatalf("expected to acquire lock, got: %v", err)
	}

	if err := lock.Acquire(); err == nil || err.Error() != "PROFILE_IN_USE" {
		t.Fatalf("expected PROFILE_IN_USE, got: %v", err)
	}

	if err := lock.Release(); err != nil {
		t.Fatalf("expected to release lock, got: %v", err)
	}

	if err := lock.Acquire(); err != nil {
		t.Fatalf("expected to re-acquire lock, got: %v", err)
	}
	lock.Release()
}

func TestLockConcurrencyConflict(t *testing.T) {
	dir := filepath.Join(os.TempDir(), "navig_lock_test_concurrent")
	defer os.RemoveAll(dir)

	lock1 := profilemgr.NewLock(dir)
	lock2 := profilemgr.NewLock(dir)

	if err := lock1.Acquire(); err != nil {
		t.Fatalf("lock1 failed to acquire: %v", err)
	}
	defer lock1.Release()

	if err := lock2.Acquire(); err == nil || err.Error() != "PROFILE_IN_USE" {
		t.Fatalf("lock2 should have failed with PROFILE_IN_USE, got: %v", err)
	}
}
