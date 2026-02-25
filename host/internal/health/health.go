// Package health provides periodic self-checks and an aggregated status.
package health

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"runtime"
	"sync"
	"sync/atomic"
	"time"

	"go.uber.org/zap"

	"navig-core/host/internal/gateway"
)

// OverallStatus is the aggregated host health.
type OverallStatus int32

const (
	StatusHealthy   OverallStatus = iota // all checks pass
	StatusDegraded                        // Python down, host operational
	StatusUnhealthy                       // host itself failing
)

func (s OverallStatus) String() string {
	switch s {
	case StatusHealthy:
		return "healthy"
	case StatusDegraded:
		return "degraded"
	case StatusUnhealthy:
		return "unhealthy"
	default:
		return "unknown"
	}
}

// CheckResult holds the outcome of a single self-check.
type CheckResult struct {
	Name    string `json:"name"`
	OK      bool   `json:"ok"`
	Message string `json:"message,omitempty"`
}

// Report is the full health report emitted every cycle.
type Report struct {
	Status  OverallStatus  `json:"status"`
	Checks  []CheckResult  `json:"checks"`
	At      time.Time      `json:"at"`
}

// Config tunes the health checker.
type Config struct {
	PollInterval    time.Duration // default: 30s
	MaxMemoryMB     int64         // default: 512
	MinDiskFreeBytes int64        // default: 100 MB
	LogDir          string
	APIAddr         string // used to verify HTTP liveness
}

func (c *Config) setDefaults() {
	if c.PollInterval == 0 {
		c.PollInterval = 30 * time.Second
	}
	if c.MaxMemoryMB == 0 {
		c.MaxMemoryMB = 512
	}
	if c.MinDiskFreeBytes == 0 {
		c.MinDiskFreeBytes = 100 * 1024 * 1024
	}
}

// Checker runs self-checks on a timer and exposes the latest Report.
type Checker struct {
	cfg       Config
	gw        *gateway.Manager
	logger    *zap.Logger
	current   atomic.Value // stores *Report
	mu        sync.Mutex
	httpProbe *http.Client
	onStatus  func(OverallStatus) // callbacks (e.g. update tray icon)
	callbacks []func(OverallStatus)
	cbMu      sync.RWMutex
}

// New creates a Checker. Call Start() to begin polling.
func New(cfg Config, gw *gateway.Manager, logger *zap.Logger) *Checker {
	cfg.setDefaults()
	c := &Checker{
		cfg:    cfg,
		gw:     gw,
		logger: logger,
		httpProbe: &http.Client{
			Timeout: 3 * time.Second,
		},
	}
	// Initialise with a degraded report so status is never nil.
	c.current.Store(&Report{Status: StatusDegraded, At: time.Now()})
	return c
}

// OnStatusChange registers a callback invoked whenever the overall status changes.
func (c *Checker) OnStatusChange(fn func(OverallStatus)) {
	c.cbMu.Lock()
	c.callbacks = append(c.callbacks, fn)
	c.cbMu.Unlock()
}

// Latest returns the most recent health report.
func (c *Checker) Latest() *Report {
	return c.current.Load().(*Report)
}

// Start begins the polling loop. Non-blocking.
func (c *Checker) Start(ctx context.Context) {
	// Run once immediately, then on ticker.
	go func() {
		c.run()
		t := time.NewTicker(c.cfg.PollInterval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				c.run()
			}
		}
	}()
}

func (c *Checker) run() {
	checks := []CheckResult{
		c.checkHTTP(),
		c.checkPython(),
		c.checkDisk(),
		c.checkMemory(),
	}

	overall := StatusHealthy
	for _, ch := range checks {
		if !ch.OK {
			if ch.Name == "python" {
				if overall < StatusDegraded {
					overall = StatusDegraded
				}
			} else {
				overall = StatusUnhealthy
			}
		}
	}

	prev := c.current.Load().(*Report)
	report := &Report{Status: overall, Checks: checks, At: time.Now()}
	c.current.Store(report)

	if prev.Status != overall {
		c.logger.Info("health status changed",
			zap.String("from", prev.Status.String()),
			zap.String("to", overall.String()),
		)
		c.cbMu.RLock()
		cbs := make([]func(OverallStatus), len(c.callbacks))
		copy(cbs, c.callbacks)
		c.cbMu.RUnlock()
		for _, fn := range cbs {
			fn(overall)
		}
	}
}

// --- individual checks -------------------------------------------------------

func (c *Checker) checkHTTP() CheckResult {
	if c.cfg.APIAddr == "" {
		return CheckResult{Name: "http", OK: true, Message: "addr not configured"}
	}
	url := "http://" + c.cfg.APIAddr + "/v1/status"
	resp, err := c.httpProbe.Get(url)
	if err != nil {
		return CheckResult{Name: "http", OK: false, Message: err.Error()}
	}
	_ = resp.Body.Close()
	if resp.StatusCode >= 500 {
		return CheckResult{Name: "http", OK: false, Message: fmt.Sprintf("status %d", resp.StatusCode)}
	}
	return CheckResult{Name: "http", OK: true}
}

func (c *Checker) checkPython() CheckResult {
	if c.gw == nil {
		return CheckResult{Name: "python", OK: false, Message: "gateway manager not set"}
	}
	s := c.gw.Status()
	ok := s == gateway.StatusHealthy
	return CheckResult{Name: "python", OK: ok, Message: s.String()}
}

func (c *Checker) checkDisk() CheckResult {
	if c.cfg.LogDir == "" {
		return CheckResult{Name: "disk", OK: true, Message: "log dir not configured"}
	}
	free, err := diskFreeBytes(c.cfg.LogDir)
	if err != nil {
		return CheckResult{Name: "disk", OK: false, Message: err.Error()}
	}
	if free < c.cfg.MinDiskFreeBytes {
		return CheckResult{
			Name:    "disk",
			OK:      false,
			Message: fmt.Sprintf("only %d MB free in %s", free>>20, c.cfg.LogDir),
		}
	}
	return CheckResult{Name: "disk", OK: true, Message: fmt.Sprintf("%d MB free", free>>20)}
}

func (c *Checker) checkMemory() CheckResult {
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)
	usedMB := int64(ms.Alloc >> 20)
	if usedMB > c.cfg.MaxMemoryMB {
		return CheckResult{
			Name:    "memory",
			OK:      false,
			Message: fmt.Sprintf("using %d MB, limit %d MB", usedMB, c.cfg.MaxMemoryMB),
		}
	}
	return CheckResult{Name: "memory", OK: true, Message: fmt.Sprintf("%d MB", usedMB)}
}

// diskFreeBytes returns available bytes in the filesystem containing dir.
func diskFreeBytes(dir string) (int64, error) {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return 0, err
	}
	return diskFree(dir)
}
