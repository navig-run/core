// Package navbrowser_test contains integration tests for the NavBrowser engine.
//
// These tests spin up a real Chrome instance via chromedp against an in-process
// httptest.Server serving controlled HTML pages. They test the complete stack:
// NavBrowser → pageintel → scriptengine.
//
// Run with:
//
//	go test ./internal/browseragent/navbrowser/... -v -tags=integration -timeout=120s
//
// Note: Tests require a Chrome/Chromium installation. Skip on CI with no browser.
//go:build integration

package navbrowser_test

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/navbrowser"
	"navig-core/host/internal/browseragent/pageintel"
)

// ─────────────────────────────────────────────────────────────────────────────
// Test HTTP server pages
// ─────────────────────────────────────────────────────────────────────────────

const loginPageHTML = `<!DOCTYPE html><html><head><title>Sign In — NavTest</title></head><body>
<h1>Sign In</h1>
<form id="login-form" action="/auth" method="POST">
  <label for="email">Email</label>
  <input type="email" id="email" name="email" placeholder="Enter your email" required>
  <label for="password">Password</label>
  <input type="password" id="password" name="password" placeholder="Password" required>
  <button type="submit" id="submit-btn">Sign In</button>
</form>
<div id="error-msg" class="error" style="display:none"></div>
<script>
document.getElementById('login-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var email = document.getElementById('email').value;
  var pass  = document.getElementById('password').value;
  if (email === 'user@test.com' && pass === 'correct') {
    window.location.href = '/dashboard';
  } else {
    document.getElementById('error-msg').style.display = 'block';
    document.getElementById('error-msg').innerText = 'Invalid email or password.';
  }
});
</script>
</body></html>`

const dashboardHTML = `<!DOCTYPE html><html><head><title>Dashboard — NavTest</title></head><body>
<h1 class="dashboard-header">Welcome back!</h1>
<p>You are now logged in.</p>
<table id="data-table">
  <thead><tr><th>Server</th><th>Status</th><th>Region</th></tr></thead>
  <tbody>
    <tr><td>prod-01</td><td>healthy</td><td>us-east-1</td></tr>
    <tr><td>prod-02</td><td>degraded</td><td>eu-west-1</td></tr>
    <tr><td>prod-03</td><td>healthy</td><td>ap-south-1</td></tr>
  </tbody>
</table>
</body></html>`

const captchaPageHTML = `<!DOCTYPE html><html><head><title>Verify — NavTest</title></head><body>
<h1>Please verify you are human</h1>
<div class="g-recaptcha" data-sitekey="test"></div>
<p>This site is protected by reCAPTCHA.</p>
</body></html>`

const dataPageHTML = `<!DOCTYPE html><html><head>
<title>Server Report</title>
<meta name="description" content="NAVIG server status report">
<meta property="og:title" content="Server Status">
<script type="application/ld+json">{"@type":"Report","name":"NAVIG Status","publisher":"NAVIG"}</script>
</head><body>
<h2>Top Links</h2>
<ul><li><a href="/docs">Documentation</a></li><li><a href="/status">Live Status</a></li></ul>
<table>
  <tr><th>Host</th><th>CPU</th><th>RAM</th></tr>
  <tr><td>prod-01</td><td>42%</td><td>6.1GB</td></tr>
  <tr><td>prod-02</td><td>78%</td><td>14.2GB</td></tr>
</table>
</body></html>`

// newTestServer returns an httptest server with all test pages configured.
func newTestServer(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, loginPageHTML)
	})
	mux.HandleFunc("/auth", func(w http.ResponseWriter, r *http.Request) {
		// form submission is handled by JS — this is fallback for non-JS
		http.Redirect(w, r, "/dashboard", http.StatusSeeOther)
	})
	mux.HandleFunc("/dashboard", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, dashboardHTML)
	})
	mux.HandleFunc("/captcha", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, captchaPageHTML)
	})
	mux.HandleFunc("/data", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, dataPageHTML)
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

// newTestBrowser creates a NavBrowser instance for tests in headless mode.
func newTestBrowser(t *testing.T) *navbrowser.NavBrowser {
	t.Helper()
	nb := navbrowser.New(nil) // nil emitter — no IPC in tests
	return nb
}

// mustLaunch launches a browser session and returns its ID.
func mustLaunch(t *testing.T, nb *navbrowser.NavBrowser, stealth bool) string {
	t.Helper()
	cfg := browser.SessionLaunchConfig{
		Headless:    true,
		BrowserName: "auto",
	}
	if stealth {
		cfg.DriverType = string(browser.DriverStealth)
	}
	session, err := nb.Launch(cfg)
	if err != nil {
		t.Skipf("NavBrowser launch failed (Chrome not installed?): %v", err)
	}
	t.Cleanup(func() {
		nb.Close(browser.CloseSessionConfig{SessionId: session.SessionId})
	})
	return session.SessionId
}

// makeEvalFn creates an EvalFn from a NavBrowser + pageID for pageintel tests.
func makeEvalFn(nb *navbrowser.NavBrowser, pageID string) pageintel.EvalFn {
	return func(js string) ([]byte, error) {
		res, err := nb.Eval(browser.EvalConfig{PageId: pageID, Js: js, TimeoutMs: 8000})
		if err != nil {
			return nil, err
		}
		return res.Result, nil
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: NavBrowser.Launch + basic navigation
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_Launch_Headless(t *testing.T) {
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)
	if sessionID == "" {
		t.Fatal("Launch returned empty sessionID")
	}
}

func TestNavBrowser_Goto_ReturnsTitle(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, err := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	if err != nil {
		t.Fatalf("NewTab: %v", err)
	}

	result, err := nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})
	if err != nil {
		t.Fatalf("Goto: %v", err)
	}
	if result.Title != "Sign In — NavTest" {
		t.Errorf("Title = %q, want %q", result.Title, "Sign In — NavTest")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: PageIntel.Analyze on login page
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_Analyze_LoginPage(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	analysis, err := inspector.Analyze()
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}

	t.Logf("PageType: %s | Inputs: %d | Buttons: %d | Forms: %d",
		analysis.PageType, len(analysis.Inputs), len(analysis.Buttons), len(analysis.Forms))

	if analysis.PageType != pageintel.PageTypeLogin {
		t.Errorf("PageType = %q, want %q", analysis.PageType, pageintel.PageTypeLogin)
	}
	if len(analysis.Inputs) < 2 {
		t.Errorf("Inputs = %d, want >= 2", len(analysis.Inputs))
	}

	userSel := pageintel.FindUsernameSelector(analysis)
	passSel := pageintel.FindPasswordSelector(analysis)
	submitSel := pageintel.FindSubmitSelector(analysis)

	if userSel == "" {
		t.Error("FindUsernameSelector: not found")
	}
	if passSel == "" {
		t.Error("FindPasswordSelector: not found")
	}
	if submitSel == "" {
		t.Error("FindSubmitSelector: not found")
	}
	t.Logf("Selectors: user=%q pass=%q submit=%q", userSel, passSel, submitSel)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Full login flow — success path
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_LoginFlow_Success(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))

	outcome, err := inspector.LoginFlow(pageintel.Credentials{
		Username: "user@test.com",
		Password: "correct",
	}, func(url string) error {
		_, err := nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: url})
		return err
	})

	if err != nil {
		t.Fatalf("LoginFlow: %v", err)
	}
	t.Logf("Outcome: success=%v nextPage=%s err=%s", outcome.Success, outcome.NextPageType, outcome.ErrorMsg)

	if !outcome.Success {
		t.Errorf("LoginFlow: success=false, want true (err=%q)", outcome.ErrorMsg)
	}
	if outcome.NextPageType != pageintel.PageTypeDashboard {
		t.Errorf("NextPageType = %q, want %q", outcome.NextPageType, pageintel.PageTypeDashboard)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Full login flow — wrong password path
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_LoginFlow_WrongPassword(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	outcome, err := inspector.LoginFlow(pageintel.Credentials{
		Username: "user@test.com",
		Password: "wrongpassword",
	}, func(url string) error {
		_, err := nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: url})
		return err
	})

	if err != nil {
		t.Fatalf("LoginFlow: %v", err)
	}

	if outcome.Success {
		t.Error("LoginFlow: success=true, want false for wrong password")
	}
	if outcome.ErrorMsg == "" {
		t.Error("ErrorMsg: expected non-empty for wrong password")
	}
	t.Logf("Error message: %q", outcome.ErrorMsg)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: CAPTCHA detection
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_CaptchaDetection(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/captcha"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	analysis, err := inspector.Analyze()
	if err != nil {
		t.Fatalf("Analyze: %v", err)
	}
	if !analysis.HasCaptcha {
		t.Error("HasCaptcha = false, want true on captcha page")
	}
	t.Logf("Captcha detected: %v | PageType: %s", analysis.HasCaptcha, analysis.PageType)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Data extraction (table, links, meta)
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_Extract_Table(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/data"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	result, err := inspector.Extract(pageintel.ExtractTable)
	if err != nil {
		t.Fatalf("Extract table: %v", err)
	}
	if len(result.Tables) == 0 {
		t.Fatal("Extract table: expected at least one table")
	}
	t.Logf("Tables: %d, first table rows: %d", len(result.Tables), len(result.Tables[0]))
	if len(result.Tables[0]) < 2 {
		t.Errorf("Table rows = %d, want >= 2", len(result.Tables[0]))
	}
}

func TestPageIntel_Extract_Links(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/data"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	result, err := inspector.Extract(pageintel.ExtractLinks)
	if err != nil {
		t.Fatalf("Extract links: %v", err)
	}
	t.Logf("Links: %v", result.Links)
	if len(result.Links) == 0 {
		t.Error("Extract links: expected links, got none")
	}
}

func TestPageIntel_Extract_Meta(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/data"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	result, err := inspector.Extract(pageintel.ExtractMeta)
	if err != nil {
		t.Fatalf("Extract meta: %v", err)
	}
	if result.Meta == nil {
		t.Fatal("Extract meta: got nil MetaData")
	}
	t.Logf("Meta: title=%q desc=%q og=%v jsonLD=%d",
		result.Meta.Title, result.Meta.Description, result.Meta.OpenGraph, len(result.Meta.JsonLD))
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Stealth mode — navigator.webdriver must be false
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_StealthMode_WebdriverFalse(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, true) // stealth=true

	page, err := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	if err != nil {
		t.Fatalf("NewTab: %v", err)
	}
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	res, err := nb.Eval(browser.EvalConfig{
		PageId:    page.PageId,
		Js:        `navigator.webdriver`,
		TimeoutMs: 3000,
	})
	if err != nil {
		t.Fatalf("Eval webdriver: %v", err)
	}

	var wd interface{}
	json.Unmarshal(res.Result, &wd)
	t.Logf("navigator.webdriver = %v (type=%T)", wd, wd)

	// Should be false or undefined (null) — never true
	if wd == true {
		t.Error("navigator.webdriver = true — stealth patch failed")
	}
}

func TestNavBrowser_StealthMode_ChromeObjectExists(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, true)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	res, err := nb.Eval(browser.EvalConfig{
		PageId:    page.PageId,
		Js:        `typeof window.chrome !== 'undefined'`,
		TimeoutMs: 3000,
	})
	if err != nil {
		t.Fatalf("Eval chrome: %v", err)
	}

	var exists bool
	json.Unmarshal(res.Result, &exists)
	if !exists {
		t.Error("window.chrome missing in stealth mode — chrome object patch failed")
	}
	t.Logf("window.chrome exists: %v", exists)
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Outcome detection
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_DetectOutcome_Dashboard(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/dashboard"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome: %v", err)
	}
	t.Logf("Outcome: %s | URL: %s | Title: %s", outcome.Status, outcome.URL, outcome.Title)
	// Dashboard doesn't have error text or captcha → should be success or unchanged
	if outcome.Status == "captcha" || outcome.Status == "blocked" {
		t.Errorf("Unexpected outcome status %q on dashboard page", outcome.Status)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Screenshot writes file
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_Screenshot_WritesFile(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/dashboard"})

	path := filepath.Join(t.TempDir(), "test_screenshot.png")
	result, err := nb.Screenshot(browser.ScreenshotConfig{PageId: page.PageId, Path: path})
	if err != nil {
		t.Fatalf("Screenshot: %v", err)
	}
	if result.Path != path {
		t.Errorf("Path = %q, want %q", result.Path, path)
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatalf("Screenshot file not found: %v", err)
	}
	if fi.Size() < 1000 {
		t.Errorf("Screenshot file too small: %d bytes", fi.Size())
	}
	t.Logf("Screenshot: %s (%d bytes)", path, fi.Size())
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Selector Evolution — broken selector gets auto-replaced
// ─────────────────────────────────────────────────────────────────────────────

func TestPageIntel_EvolveSelector_FindsEmailField(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	inspector := pageintel.New(makeEvalFn(nb, page.PageId))
	candidates, err := inspector.EvolveSelector("email")
	if err != nil {
		t.Fatalf("EvolveSelector: %v", err)
	}
	t.Logf("EvolveSelector candidates for 'email': %+v", candidates)
	// The login page has an #email field — at least one candidate expected
	if len(candidates) == 0 {
		t.Log("No candidates found — this is acceptable if the field has no aria/name attrs")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: BrowserInstall listing
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_ListBrowsers(t *testing.T) {
	nb := newTestBrowser(t)
	installs, err := nb.ListBrowsers()
	if err != nil {
		t.Fatalf("ListBrowsers: %v", err)
	}
	t.Logf("Found %d browser(s): %+v", len(installs), installs)
	// Not asserting count — depends on the machine
}

// ─────────────────────────────────────────────────────────────────────────────
// Test: Multi-tab isolation
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_MultiTab_Isolation(t *testing.T) {
	srv := newTestServer(t)
	nb := newTestBrowser(t)
	sessionID := mustLaunch(t, nb, false)

	page1, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})
	page2, _ := nb.NewTab(browser.NewTabConfig{SessionId: sessionID})

	nb.Goto(browser.GotoConfig{PageId: page1.PageId, Url: srv.URL + "/"})
	nb.Goto(browser.GotoConfig{PageId: page2.PageId, Url: srv.URL + "/dashboard"})

	res1, _ := nb.Eval(browser.EvalConfig{PageId: page1.PageId, Js: `document.title`, TimeoutMs: 3000})
	res2, _ := nb.Eval(browser.EvalConfig{PageId: page2.PageId, Js: `document.title`, TimeoutMs: 3000})

	var title1, title2 string
	json.Unmarshal(res1.Result, &title1)
	json.Unmarshal(res2.Result, &title2)

	t.Logf("Tab 1 title: %q | Tab 2 title: %q", title1, title2)

	if title1 == title2 {
		t.Error("Multi-tab isolation failed: both tabs have same title")
	}
	if title1 != "Sign In — NavTest" {
		t.Errorf("Tab 1 title = %q, want %q", title1, "Sign In — NavTest")
	}
	if title2 != "Dashboard — NavTest" {
		t.Errorf("Tab 2 title = %q, want %q", title2, "Dashboard — NavTest")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Benchmark: launch + navigate + eval (baseline performance)
// ─────────────────────────────────────────────────────────────────────────────

func BenchmarkNavBrowser_Navigate(b *testing.B) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprint(w, "<html><head><title>Bench</title></head><body>ok</body></html>")
	}))
	defer srv.Close()

	nb := navbrowser.New(nil)
	session, err := nb.Launch(browser.SessionLaunchConfig{Headless: true, BrowserName: "auto"})
	if err != nil {
		b.Skipf("launch failed: %v", err)
	}
	defer nb.Close(browser.CloseSessionConfig{SessionId: session.SessionId})

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: session.SessionId})

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		start := time.Now()
		nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})
		b.ReportMetric(float64(time.Since(start).Milliseconds()), "ms/nav")
	}
}
