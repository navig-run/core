package ipc_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/chromedpdriver"
	"navig-core/host/internal/browseragent/ipc"
	"navig-core/host/internal/browseragent/playwrightdriver"
	"navig-core/host/internal/browseragent/profilemgr"
	"navig-core/host/internal/browseragent/router"
)

func TestIntegrationTaskRun(t *testing.T) {
	// 1. HTTP Server
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		w.Write([]byte(`<html><head><title>Integration Test</title></head><body><h1>Hello World</h1></body></html>`))
	}))
	defer ts.Close()

	// 2. Setup router and registry
	reg, err := profilemgr.NewRegistry()
	if err != nil {
		t.Fatalf("Failed to create registry: %v", err)
	}

	mu := &sync.Mutex{}
	emitter := ipc.NewStdoutEmitter(mu)
	cdp := chromedpdriver.New(emitter)
	pw := playwrightdriver.New(emitter)

	cdpFactory := func(_ interface{}) browser.Driver { return cdp }
	pwFactory := func(_ interface{}) browser.Driver { return pw }
	taskRouter := router.New(reg, cdpFactory, pwFactory)

	// 3. Setup Request
	req := browser.TaskRunRequest{
		TaskID: "test-task-123",
		Routing: struct {
			Profile  string `json:"profile"`
			Engine   string `json:"engine"`
			Browser  string `json:"browser"`
			Headless bool   `json:"headless"`
		}{
			Profile:  "crypto",
			Engine:   "chromedp",
			Headless: true,
		},
		Steps: []browser.TaskStep{
			{Goto: &browser.StepGoto{URL: ts.URL}},
			{Wait: &browser.StepWait{Kind: "dom_ready", TimeoutMs: 2000}},
			{Screenshot: &browser.StepScreenshot{}},
		},
	}

	// 4. Exec Task
	policy := router.RouterPolicy{AllowFallbackOnTimeout: false, CloneOnBusy: false}
	res, err := taskRouter.ExecuteTask(context.Background(), req, emitter, policy.AllowFallbackOnTimeout, policy.CloneOnBusy)
	if err != nil {
		t.Fatalf("ExecuteTask failed: %v", err)
	}

	// 5. Assertions
	// The router reads the real page title via chromedp; the httptest server serves
	// <title>Integration Test</title> — that is what we assert.
	if res.Title != "Integration Test" {
		t.Errorf("expected page title 'Integration Test', got %q", res.Title)
	}
	if res.EngineUsed != "chromedp" {
		t.Errorf("expected engine 'chromedp', got %q", res.EngineUsed)
	}
	if res.ProfileUsed != "crypto" {
		t.Errorf("expected profile 'crypto', got %q", res.ProfileUsed)
	}
	if res.FinalURL == "" {
		t.Error("expected non-empty FinalURL")
	}
	if res.Lifecycle.StepCount != len(req.Steps) {
		t.Errorf("expected %d steps recorded, got %d", len(req.Steps), res.Lifecycle.StepCount)
	}

	// Artifact dir check — router.go always writes page.json and a final screenshot
	home, _ := os.UserHomeDir()
	artDir := filepath.Join(home, ".navig", "browser", "artifacts", "test-task-123")
	if _, err := os.Stat(filepath.Join(artDir, "page.json")); os.IsNotExist(err) {
		t.Error("page.json artifact not created")
	}
	if _, err := os.Stat(filepath.Join(artDir, "final_screenshot.png")); os.IsNotExist(err) {
		t.Error("final_screenshot.png artifact not created")
	}
}

func TestConcurrencyProfileInUse(t *testing.T) {
	// Prepare simple registry lock
	home, _ := os.UserHomeDir()
	dir := filepath.Join(home, ".navig", "browser", "profiles", "concurrency-test")
	os.MkdirAll(dir, 0700)

	lock1 := profilemgr.NewLock(dir)
	lock2 := profilemgr.NewLock(dir)

	if err := lock1.Acquire(); err != nil {
		t.Fatalf("First lock should acquire cleanly, got: %v", err)
	}
	defer lock1.Release()

	if err := lock2.Acquire(); err == nil {
		t.Error("Second lock should fail due to PROFILE_IN_USE")
	} else if err.Error() != "PROFILE_IN_USE" {
		t.Errorf("Expected PROFILE_IN_USE, got %v", err)
	}
}
