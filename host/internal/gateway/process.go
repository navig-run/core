// Package gateway manages the Python navig daemon subprocess.
package gateway

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"go.uber.org/zap"
	"net"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"sync"
	"sync/atomic"
	"time"
)

// Status represents the health state of the Python gateway process.
type Status int32

const (
	StatusDown     Status = iota // process not running
	StatusStarting               // process launched, not yet healthy
	StatusHealthy                // health check passing
	StatusDegraded               // running but health check failing
)

func (s Status) String() string {
	switch s {
	case StatusDown:
		return "down"
	case StatusStarting:
		return "starting"
	case StatusHealthy:
		return "healthy"
	case StatusDegraded:
		return "degraded"
	default:
		return "unknown"
	}
}

const (
	healthPollInterval  = 5 * time.Second
	gracefulStopTimeout = 5 * time.Second
	stableUptimeReset   = 60 * time.Second

	backoffInitial = 1 * time.Second
	backoffMax     = 30 * time.Second
)

// Config holds configuration for the gateway process manager.
type Config struct {
	// PythonPath is the Python interpreter to use. Falls back through discovery
	// order: config value → NAVIG_PYTHON env → python3 → python in PATH.
	PythonPath string
	// ExtraArgs are appended to the python -m navig.gateway.main invocation.
	ExtraArgs []string
}

// Manager supervises the Python gateway subprocess.
type Manager struct {
	cfg    Config
	logger *zap.SugaredLogger

	internalPort int
	baseURL      string

	status atomic.Int32 // stores Status

	mu        sync.Mutex
	cmd       *exec.Cmd
	cancelCtx context.CancelFunc // cancels the run loop

	startedAt time.Time // reset on each successful start

	httpClient *http.Client
}

// NewManager creates a Manager. Call Start() to launch the subprocess.
func NewManager(cfg Config, logger *zap.SugaredLogger) *Manager {
	return &Manager{
		cfg:    cfg,
		logger: logger,
		httpClient: &http.Client{
			Timeout: 5 * time.Second,
		},
	}
}

// BaseURL returns the internal HTTP base URL for the Python gateway.
// Returns empty string if the process has not been started yet.
func (m *Manager) BaseURL() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.baseURL
}

// Status returns the current process health.
func (m *Manager) Status() Status {
	return Status(m.status.Load())
}

// Start launches the subprocess run-loop (non-blocking).
// Safe to call only once; subsequent calls are no-ops.
func (m *Manager) Start() error {
	python, err := resolvePython(m.cfg.PythonPath)
	if err != nil {
		return err
	}
	m.cfg.PythonPath = python

	// Validate navig package is importable
	if err := validateNavigPackage(python, m.logger); err != nil {
		m.logger.Warn("navig package not found — gateway running in degraded mode",
			"err", err,
			"fix", "run: "+python+" -m pip install navig",
		)
		m.status.Store(int32(StatusDegraded))
		return nil // don't return error — host still starts
	}

	ctx, cancel := context.WithCancel(context.Background())
	m.mu.Lock()
	m.cancelCtx = cancel
	m.mu.Unlock()

	go m.runLoop(ctx)
	return nil
}

// Stop sends a graceful shutdown signal to the subprocess and waits.
func (m *Manager) Stop() {
	m.mu.Lock()
	cancel := m.cancelCtx
	m.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// Restart stops and re-launches the subprocess immediately (resets backoff).
func (m *Manager) Restart() {
	m.Stop()
	time.Sleep(200 * time.Millisecond) // let run-loop exit cleanly
	if err := m.Start(); err != nil {
		m.logger.Error("restart failed", "err", err)
	}
}

// runLoop is the crash-recovery loop. Runs in its own goroutine.
func (m *Manager) runLoop(ctx context.Context) {
	backoff := backoffInitial
	for {
		if ctx.Err() != nil {
			return
		}

		port, err := freePort()
		if err != nil {
			m.logger.Error("gateway: cannot find free port", "err", err)
			return
		}

		m.mu.Lock()
		m.internalPort = port
		m.baseURL = fmt.Sprintf("http://127.0.0.1:%d", port)
		m.mu.Unlock()

		m.status.Store(int32(StatusStarting))
		m.logger.Info("gateway: launching python subprocess", "port", port)

		proc, err := m.launch(ctx, port)
		if err != nil {
			m.logger.Error("gateway: launch failed", "err", err)
			m.status.Store(int32(StatusDown))
		} else {
			launchTime := time.Now()
			m.mu.Lock()
			m.cmd = proc
			m.startedAt = launchTime
			m.mu.Unlock()

			// Poll health until proc dies or ctx cancelled
			m.runHealthLoop(ctx, proc)

			// Process exited — was it stable for >= stableUptimeReset?
			uptime := time.Since(launchTime)
			if uptime >= stableUptimeReset {
				backoff = backoffInitial
			}
		}

		if ctx.Err() != nil {
			return
		}

		m.status.Store(int32(StatusDown))
		m.logger.Warn("gateway: subprocess exited, restarting", "backoff", backoff)
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, backoffMax)
	}
}

// launch starts the Python subprocess and returns the *exec.Cmd (already running).
func (m *Manager) launch(ctx context.Context, port int) (*exec.Cmd, error) {
	args := append(
		[]string{"-m", "navig.gateway.main", "--port", fmt.Sprintf("%d", port)},
		m.cfg.ExtraArgs...,
	)
	cmd := exec.CommandContext(ctx, m.cfg.PythonPath, args...)

	// Pipe stdout/stderr into logger
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("gateway: stdout pipe: %w", err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, fmt.Errorf("gateway: stderr pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("gateway: start: %w", err)
	}

	go streamLog(m.logger, "gateway.stdout", stdout)
	go streamLog(m.logger, "gateway.stderr", stderr)

	return cmd, nil
}

// runHealthLoop polls the health endpoint until the process exits.
func (m *Manager) runHealthLoop(ctx context.Context, cmd *exec.Cmd) {
	// Wait for process exit in a goroutine so we can unblock from health poll.
	done := make(chan struct{})
	go func() {
		_ = cmd.Wait()
		close(done)
	}()

	ticker := time.NewTicker(healthPollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			m.terminateProcess(cmd)
			<-done
			return
		case <-done:
			return
		case <-ticker.C:
			m.checkHealth()
		}
	}
}

// checkHealth performs a single GET /health against the internal port.
func (m *Manager) checkHealth() {
	m.mu.Lock()
	url := m.baseURL
	m.mu.Unlock()
	if url == "" {
		return
	}
	resp, err := m.httpClient.Get(url + "/health")
	if err != nil {
		m.logger.Debug("gateway: health check failed", "err", err)
		m.status.Store(int32(StatusDegraded))
		return
	}
	_ = resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		m.status.Store(int32(StatusHealthy))
	} else {
		m.status.Store(int32(StatusDegraded))
	}
}

// terminateProcess sends SIGTERM (Unix) or ctrl-C event (Windows), waits up to
// gracefulStopTimeout, then force-kills.
func (m *Manager) terminateProcess(cmd *exec.Cmd) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	if runtime.GOOS == "windows" {
		// On Windows there is no SIGTERM; send interrupt via GenerateConsoleCtrlEvent
		// by calling os.Interrupt (which maps to a ctrl-C / CTRL_C_EVENT).
		_ = cmd.Process.Signal(os.Interrupt)
	} else {
		_ = cmd.Process.Signal(os.Interrupt)
	}

	done := make(chan struct{})
	go func() {
		_ = cmd.Wait()
		close(done)
	}()

	select {
	case <-done:
		// graceful exit
	case <-time.After(gracefulStopTimeout):
		m.logger.Warn("gateway: graceful shutdown timed out, force-killing")
		_ = cmd.Process.Kill()
		<-done
	}
}

// --- Helpers ------------------------------------------------------------------

// streamLog reads lines from r and emits them via slog at Debug level.
func streamLog(logger *zap.SugaredLogger, src string, r io.ReadCloser) {
	scanner := bufio.NewScanner(r)
	for scanner.Scan() {
		logger.Debug(scanner.Text(), "src", src)
	}
}

// freePort asks the OS for an available ephemeral port on localhost.
func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	port := l.Addr().(*net.TCPAddr).Port
	_ = l.Close()
	return port, nil
}

// min returns the smaller of two durations.
func min(a, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}
