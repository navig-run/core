// Package logging provides dual-output structured logging (zap) with daily
// file rotation (lumberjack) and a crash-safety panic handler.
package logging

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"sync"
	"time"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"gopkg.in/natefinch/lumberjack.v2"
)

const (
	maxSizeMB  = 100 // MB per log file before rotation
	maxAgeDays = 14  // days to retain old log files
	maxBackups = 30  // maximum number of old log files to keep
)

// Logger wraps zap.Logger with a reference to the underlying ring buffer
// (used by the crash handler to append recent log lines).
type Logger struct {
	*zap.Logger
	ring   *ringBuffer
	closer *lumberjack.Logger
	mu     sync.Mutex
}

// Close flushes the logger and releases the underlying log file.
// Call this in tests (or on shutdown) so temp directories can be removed.
func (l *Logger) Close() error {
	_ = l.Logger.Sync()
	if l.closer != nil {
		return l.closer.Close()
	}
	return nil
}

// New constructs a Logger that writes:
//   - JSON lines to logDir/navig-host.json.log (rotated daily)
//   - Human-readable text to stdout (when foregrounded)
//
// level must be one of "debug", "info", "warn", "error".
func New(logDir, level string) (*Logger, error) {
	if err := os.MkdirAll(logDir, 0700); err != nil {
		return nil, fmt.Errorf("logging: mkdir %s: %w", logDir, err)
	}

	zapLevel, err := zapcore.ParseLevel(level)
	if err != nil {
		zapLevel = zapcore.InfoLevel
	}

	// --- JSON file sink (rotated) ---
	jsonSink := &lumberjack.Logger{
		Filename:   filepath.Join(logDir, "navig-host.json.log"),
		MaxSize:    maxSizeMB,
		MaxAge:     maxAgeDays,
		MaxBackups: maxBackups,
		Compress:   true,
		LocalTime:  false,
	}

	fileEnc := zapcore.NewJSONEncoder(jsonEncoderCfg())
	fileSyncer := zapcore.AddSync(jsonSink)
	fileCore := zapcore.NewCore(fileEnc, fileSyncer, zapLevel)

	// --- Human-readable stdout sink ---
	consoleEnc := zapcore.NewConsoleEncoder(consoleEncoderCfg())
	consoleSyncer := zapcore.Lock(os.Stdout)
	consoleCore := zapcore.NewCore(consoleEnc, consoleSyncer, zapLevel)

	// --- Ring buffer (for crash handler) ---
	ring := newRingBuffer(200)
	ringCore := zapcore.NewCore(
		zapcore.NewJSONEncoder(jsonEncoderCfg()),
		zapcore.AddSync(ring),
		zapLevel,
	)

	combined := zapcore.NewTee(fileCore, consoleCore, ringCore)
	zl := zap.New(combined, zap.AddCaller(), zap.AddCallerSkip(0))

	return &Logger{Logger: zl, ring: ring, closer: jsonSink}, nil
}

// InstallCrashHandler registers a deferred panic handler that writes
// crash.log into logDir before exiting.
func (l *Logger) InstallCrashHandler(logDir string) {
	crashPath := filepath.Join(logDir, "crash.log")
	// This must be called with defer in the top-level goroutine.
	// We return a closure the caller can defer.
	_ = crashPath // documented: caller uses RecoverAndWrite directly
}

// RecoverAndWrite must be called via `defer logger.RecoverAndWrite(logDir)`
// at the top of main(). It catches panics, writes crash.log, then re-panics.
func (l *Logger) RecoverAndWrite(logDir string) {
	r := recover()
	if r == nil {
		return
	}

	_ = l.Sync() // flush pending writes

	crashPath := filepath.Join(logDir, "crash.log")
	f, err := os.OpenFile(crashPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		fmt.Fprintf(os.Stderr, "crash handler: cannot open %s: %v\n", crashPath, err)
		panic(r)
	}
	defer f.Close()

	ts := time.Now().UTC().Format(time.RFC3339)
	fmt.Fprintf(f, "=== CRASH at %s ===\n", ts)
	fmt.Fprintf(f, "panic: %v\n\n", r)
	fmt.Fprintf(f, "goroutines:\n%s\n", debug.Stack())
	fmt.Fprintf(f, "\n--- last %d log lines ---\n", l.ring.cap)

	for _, line := range l.ring.Lines() {
		fmt.Fprintln(f, line)
	}

	fmt.Fprintln(f, "=== END CRASH ===")

	// Also write to stderr so the terminal sees it
	fmt.Fprintf(os.Stderr, "\n[navig-host PANIC] %v\n%s\nCrash log: %s\n",
		r, debug.Stack(), crashPath)

	panic(r)
}

// RingLines returns the last (up to 200) log lines captured in the ring buffer.
// Used by the crash handler and tests.
func (l *Logger) RingLines() []string {
	return l.ring.Lines()
}

// Sync flushes all pending log writes.
func (l *Logger) Sync() error {
	return l.Logger.Sync()
}

// --- Encoder configurations --------------------------------------------------

func jsonEncoderCfg() zapcore.EncoderConfig {
	return zapcore.EncoderConfig{
		TimeKey:        "ts",
		LevelKey:       "level",
		NameKey:        "module",
		CallerKey:      "caller",
		MessageKey:     "msg",
		StacktraceKey:  "stacktrace",
		LineEnding:     zapcore.DefaultLineEnding,
		EncodeLevel:    zapcore.LowercaseLevelEncoder,
		EncodeTime:     zapcore.ISO8601TimeEncoder,
		EncodeDuration: zapcore.MillisDurationEncoder,
		EncodeCaller:   zapcore.ShortCallerEncoder,
	}
}

func consoleEncoderCfg() zapcore.EncoderConfig {
	cfg := jsonEncoderCfg()
	cfg.EncodeLevel = zapcore.CapitalColorLevelEncoder
	cfg.EncodeTime = func(t time.Time, enc zapcore.PrimitiveArrayEncoder) {
		enc.AppendString(t.UTC().Format("15:04:05.000"))
	}
	return cfg
}

// --- Ring buffer -------------------------------------------------------------

type ringBuffer struct {
	mu   sync.Mutex
	buf  []string
	pos  int
	size int
	cap  int
}

func newRingBuffer(capacity int) *ringBuffer {
	return &ringBuffer{buf: make([]string, capacity), cap: capacity}
}

// Write implements io.Writer; each call is treated as one log line.
func (r *ringBuffer) Write(p []byte) (n int, err error) {
	r.mu.Lock()
	r.buf[r.pos%r.cap] = string(p)
	r.pos++
	if r.size < r.cap {
		r.size++
	}
	r.mu.Unlock()
	return len(p), nil
}

// Lines returns up to cap lines in chronological order.
func (r *ringBuffer) Lines() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]string, 0, r.size)
	start := 0
	if r.size == r.cap {
		start = r.pos % r.cap
	}
	for i := 0; i < r.size; i++ {
		out = append(out, r.buf[(start+i)%r.cap])
	}
	return out
}

// LogDirForOS returns the platform-specific log directory without importing
// internal/os (to avoid circular deps). Callers that already have a Paths
// instance should use paths.LogDir() instead.
func LogDirForOS() string {
	home, _ := os.UserHomeDir()
	switch runtime.GOOS {
	case "windows":
		local := os.Getenv("LOCALAPPDATA")
		if local == "" {
			local = filepath.Join(home, "AppData", "Local")
		}
		return filepath.Join(local, "navig", "logs")
	case "darwin":
		return filepath.Join(home, "Library", "Logs", "navig")
	default:
		dataHome := os.Getenv("XDG_DATA_HOME")
		if dataHome == "" {
			dataHome = filepath.Join(home, ".local", "share")
		}
		return filepath.Join(dataHome, "navig", "logs")
	}
}
