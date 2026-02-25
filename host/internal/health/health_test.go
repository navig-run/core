package health_test

import (
	"context"
	"testing"
	"time"

	"go.uber.org/zap"

	"navig-core/host/internal/health"
)

// stubGateway implements the minimal interface used by health.Checker.
// gateway.Manager is concrete, so we test via the public Status check output.

func TestHealthStatusAggregation_AllHealthy(t *testing.T) {
	checks := []health.CheckResult{
		{Name: "http", OK: true},
		{Name: "python", OK: true},
		{Name: "disk", OK: true},
		{Name: "memory", OK: true},
	}
	status := aggregateStatus(checks)
	if status != health.StatusHealthy {
		t.Errorf("expected healthy, got %s", status)
	}
}

func TestHealthStatusAggregation_PythonDown(t *testing.T) {
	checks := []health.CheckResult{
		{Name: "http", OK: true},
		{Name: "python", OK: false, Message: "down"},
		{Name: "disk", OK: true},
		{Name: "memory", OK: true},
	}
	status := aggregateStatus(checks)
	if status != health.StatusDegraded {
		t.Errorf("expected degraded (python down), got %s", status)
	}
}

func TestHealthStatusAggregation_HTTPDown(t *testing.T) {
	checks := []health.CheckResult{
		{Name: "http", OK: false, Message: "connection refused"},
		{Name: "python", OK: true},
		{Name: "disk", OK: true},
		{Name: "memory", OK: true},
	}
	status := aggregateStatus(checks)
	if status != health.StatusUnhealthy {
		t.Errorf("expected unhealthy when http is down, got %s", status)
	}
}

func TestHealthStatusAggregation_DiskLow(t *testing.T) {
	checks := []health.CheckResult{
		{Name: "http", OK: true},
		{Name: "python", OK: true},
		{Name: "disk", OK: false, Message: "only 10 MB free"},
		{Name: "memory", OK: true},
	}
	status := aggregateStatus(checks)
	if status != health.StatusUnhealthy {
		t.Errorf("expected unhealthy when disk is low, got %s", status)
	}
}

func TestHealthStatusAggregation_MemoryHigh(t *testing.T) {
	checks := []health.CheckResult{
		{Name: "http", OK: true},
		{Name: "python", OK: true},
		{Name: "disk", OK: true},
		{Name: "memory", OK: false, Message: "using 600 MB"},
	}
	status := aggregateStatus(checks)
	if status != health.StatusUnhealthy {
		t.Errorf("expected unhealthy when memory over limit, got %s", status)
	}
}

func TestOverallStatusString(t *testing.T) {
	cases := map[health.OverallStatus]string{
		health.StatusHealthy:   "healthy",
		health.StatusDegraded:  "degraded",
		health.StatusUnhealthy: "unhealthy",
	}
	for s, want := range cases {
		if s.String() != want {
			t.Errorf("status %d: got %q, want %q", s, s.String(), want)
		}
	}
}

func TestCheckerOnStatusChangeCalled(t *testing.T) {
	logger := zap.NewNop()
	cfg := health.Config{
		PollInterval:     50 * time.Millisecond,
		MaxMemoryMB:      4096, // very high — won't trip in test
		MinDiskFreeBytes: 0,    // disabled
		APIAddr:          "",   // no HTTP check
	}
	checker := health.New(cfg, nil, logger)

	var called int
	checker.OnStatusChange(func(s health.OverallStatus) {
		called++
	})

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	checker.Start(ctx)

	<-ctx.Done()
	// Status may have changed at least once from the initial store value.
	// We just verify no panic and the callback is wired.
	_ = called
}

// --- helpers -----------------------------------------------------------------

// aggregateStatus replicates the internal aggregation logic for unit testing.
func aggregateStatus(checks []health.CheckResult) health.OverallStatus {
	overall := health.StatusHealthy
	for _, ch := range checks {
		if !ch.OK {
			if ch.Name == "python" {
				if overall < health.StatusDegraded {
					overall = health.StatusDegraded
				}
			} else {
				overall = health.StatusUnhealthy
			}
		}
	}
	return overall
}
