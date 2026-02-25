package metrics_test

import (
	"sync"
	"testing"

	"navig-core/host/internal/metrics"
)

func TestIncrementRequestsTotal(t *testing.T) {
	c := metrics.New()
	for i := 0; i < 5; i++ {
		c.IncRequests()
	}
	snap := c.Snapshot()
	if snap["requests_total"] != 5 {
		t.Errorf("requests_total: got %d, want 5", snap["requests_total"])
	}
}

func TestIncrementErrorsTotal(t *testing.T) {
	c := metrics.New()
	c.IncErrors()
	c.IncErrors()
	snap := c.Snapshot()
	if snap["errors_total"] != 2 {
		t.Errorf("errors_total: got %d, want 2", snap["errors_total"])
	}
}

func TestIncrementPythonRestarts(t *testing.T) {
	c := metrics.New()
	c.IncPythonRestarts()
	snap := c.Snapshot()
	if snap["python_restarts_total"] != 1 {
		t.Errorf("python_restarts_total: got %d, want 1", snap["python_restarts_total"])
	}
}

func TestUptimeNonNegative(t *testing.T) {
	c := metrics.New()
	if c.UptimeSeconds() < 0 {
		t.Error("uptime_seconds must be >= 0")
	}
}

func TestConcurrentIncrement(t *testing.T) {
	c := metrics.New()
	var wg sync.WaitGroup
	const goroutines = 100
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.IncRequests()
		}()
	}
	wg.Wait()
	snap := c.Snapshot()
	if snap["requests_total"] != goroutines {
		t.Errorf("concurrent: got %d, want %d", snap["requests_total"], goroutines)
	}
}

func TestEndpointCounters(t *testing.T) {
	ec := metrics.NewEndpointCounters()
	ec.Inc("/v1/status")
	ec.Inc("/v1/status")
	ec.Inc("/v1/inbox/ingest")
	ec.Inc("/v1/unknown") // no-op

	snap := ec.Snapshot()
	if snap["/v1/status"] != 2 {
		t.Errorf("/v1/status: got %d, want 2", snap["/v1/status"])
	}
	if snap["/v1/inbox/ingest"] != 1 {
		t.Errorf("/v1/inbox/ingest: got %d, want 1", snap["/v1/inbox/ingest"])
	}
	if _, ok := snap["/v1/unknown"]; ok {
		t.Error("/v1/unknown should not appear in snapshot")
	}
}
