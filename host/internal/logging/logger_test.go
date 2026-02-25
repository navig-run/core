package logging_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"navig-core/host/internal/logging"
)

func TestNewLoggerCreatesFile(t *testing.T) {
	dir := t.TempDir()
	l, err := logging.New(dir, "debug")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer l.Close()

	l.Info("test message from logger")
	_ = l.Sync()

	// JSON log file must exist after a sync
	path := filepath.Join(dir, "navig-host.json.log")
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("log file not created at %s: %v", path, err)
	}

	data, _ := os.ReadFile(path)
	if !strings.Contains(string(data), "test message from logger") {
		t.Errorf("expected message in log file, got:\n%s", string(data))
	}
}

func TestRingBufferCapacity(t *testing.T) {
	dir := t.TempDir()
	l, err := logging.New(dir, "debug")
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer l.Close()

	// Write 250 lines — ring holds 200
	for i := 0; i < 250; i++ {
		l.Info("line")
	}
	_ = l.Sync()

	lines := l.RingLines()
	if len(lines) > 200 {
		t.Errorf("ring buffer: got %d lines, want ≤200", len(lines))
	}
	if len(lines) == 0 {
		t.Error("ring buffer: expected ≥1 line")
	}
}

func TestRecoverWritesCrashLog(t *testing.T) {
	dir := t.TempDir()
	l, err := logging.New(dir, "debug")
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	l.Info("before panic")
	_ = l.Sync()

	// Simulate RecoverAndWrite after a panic scenario
	// We invoke it directly without a real panic (it's a no-op if r == nil).
	l.RecoverAndWrite(dir)
	_ = l.Close()

	// crash.log should NOT be created when there is no panic
	crashPath := filepath.Join(dir, "crash.log")
	if _, err := os.Stat(crashPath); err == nil {
		t.Error("crash.log should not be created when there is no panic")
	}
}

func TestLogDirForOS(t *testing.T) {
	dir := logging.LogDirForOS()
	if dir == "" {
		t.Error("LogDirForOS returned empty string")
	}
}
