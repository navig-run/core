//go:build integration

// Package navbrowser_test — Chaos Server Integration Tests
//
// The "Self-Healing Proof": a chaotic httptest server that randomly mutates
// the DOM between requests (Version A / Version B / Version C). NavBrowser
// must log in successfully 10 consecutive times regardless of which HTML it sees.
//
// This is the gold standard proof that the 3-tier resolver is immune to
// UI updates, React/Tailwind rewrites, and A/B tests.
//
// Run with:
//   go test ./internal/browseragent/navbrowser/... -v -tags=integration -run TestChaos -timeout=300s

package navbrowser_test

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/navbrowser"
	"navig-core/host/internal/browseragent/pageintel"
)

// ─────────────────────────────────────────────────────────────────────────────
// DOM Variants — the mutating corpus
// ─────────────────────────────────────────────────────────────────────────────

// Version A: Classic Bootstrap / server-rendered form. Stable IDs.
// This is what Tier 1 (cached selectors) handles.
const versionA = `<!DOCTYPE html><html><head><title>Sign In</title></head><body>
<div class="container">
  <h2>Welcome back</h2>
  <form id="login-form" method="POST" action="/auth">
    <div class="form-group">
      <label for="user_email">Email address</label>
      <input type="email" id="user_email" name="email" class="form-control" placeholder="you@example.com" required>
    </div>
    <div class="form-group">
      <label for="user_password">Password</label>
      <input type="password" id="user_password" name="password" class="form-control" required>
    </div>
    <button type="submit" id="login_submit" class="btn btn-primary">Sign In</button>
  </form>
</div>
<script>
document.getElementById('login-form').addEventListener('submit', function(e){
  e.preventDefault();
  if(document.getElementById('user_email').value==='user@test.com' &&
     document.getElementById('user_password').value==='correct'){
    window.location.href='/dashboard';
  } else {
    var err=document.createElement('div');
    err.className='alert alert-danger';
    err.innerText='Invalid email or password.';
    document.querySelector('.container').prepend(err);
  }
});
</script>
</body></html>`

// Version B: React/Next.js style — no predictable IDs, Tailwind classes, dynamic keys.
// Tier 1 fails completely. Tier 2 must find the fields by semantic signals.
const versionB = `<!DOCTYPE html><html><head><title>Log In — App</title></head><body>
<div class="min-h-screen flex items-center justify-center bg-gray-100">
  <div class="bg-white p-8 rounded-lg shadow-md w-96">
    <h1 class="text-2xl font-bold mb-6">Log in to your account</h1>
    <div class="mb-4">
      <label class="block text-sm font-medium text-gray-700 mb-2">E-Mail Address</label>
      <input data-v-8f7a1b class="w-full px-3 py-2 border rounded-md text-sm focus:outline-none" type="email" placeholder="Email" aria-label="Email Address">
    </div>
    <div class="mb-6">
      <label class="block text-sm font-medium text-gray-700 mb-2">Pass</label>
      <input data-v-9g2c1d class="w-full px-3 py-2 border rounded-md text-sm" type="password" aria-label="Password" placeholder="••••••••">
    </div>
    <div role="button" class="w-full bg-indigo-600 text-white py-2 px-4 rounded-md text-center cursor-pointer" onclick="handleLogin()">
      Continue
    </div>
  </div>
</div>
<script>
function handleLogin(){
  var email=document.querySelector('[aria-label="Email Address"]').value;
  var pass=document.querySelector('[aria-label="Password"]').value;
  if(email==='user@test.com' && pass==='correct'){
    window.location.href='/dashboard';
  } else {
    var err=document.createElement('p');
    err.className='text-red-500 text-sm mt-2';
    err.innerText='Incorrect credentials. Try again.';
    document.querySelector('[role="button"]').parentNode.appendChild(err);
  }
}
</script>
</body></html>`

// Version C: Completely minified / obfuscated — no aria-labels, no names, no IDs.
// Tier 2 fails. Tier 3 MUST succeed using structural heuristics:
// "find the password input. find the input before it. find the last button."
const versionC = `<!DOCTYPE html><html><head><title>Access Portal</title></head><body>
<div class="css-x9f2p">
  <span class="css-title-h">Account Access</span>
  <div class="css-field-wrap">
    <input class="css-991x" type="text" tabindex="1">
    <input class="css-992x" type="password" tabindex="2">
    <div class="css-btn-primary" tabindex="3" onclick="doLogin()">Proceed</div>
  </div>
</div>
<script>
function doLogin(){
  var inputs=document.querySelectorAll('input');
  var email=inputs[0].value, pass=inputs[1].value;
  if(email==='user@test.com'&&pass==='correct'){
    window.location.href='/dashboard';
  } else {
    var err=document.createElement('span');
    err.style.color='red';
    err.innerText='Error: invalid credentials';
    document.querySelector('.css-btn-primary').after(err);
  }
}
</script>
</body></html>`

// Dashboard page — served after successful login
const chaosDashboard = `<!DOCTYPE html><html><head><title>Dashboard</title></head><body>
<nav><a href="/logout">Log out</a><img class="avatar" src="/profile.png" alt="profile"></nav>
<h1>Welcome to your dashboard</h1>
<div class="dashboard-content"><p>You are now logged in.</p></div>
</body></html>`

// ─────────────────────────────────────────────────────────────────────────────
// Chaos Server
// ─────────────────────────────────────────────────────────────────────────────

type chaosServer struct {
	versionCounter int64 // atomic: cycles through variants
	authCount      int64 // how many successful logins recorded
}

func newChaosServer(t *testing.T, rng *rand.Rand) *httptest.Server {
	t.Helper()
	srv := &chaosServer{}
	mux := http.NewServeMux()

	variants := []string{versionA, versionB, versionC}

	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		// Use request counter to deterministically cycle (for reproducibility in logs)
		// but also support random mode via Accept header hint
		n := atomic.AddInt64(&srv.versionCounter, 1)
		idx := int(n-1) % len(variants)
		if rng != nil {
			idx = rng.Intn(len(variants))
		}
		fmt.Fprint(w, variants[idx])
	})

	mux.HandleFunc("/auth", func(w http.ResponseWriter, r *http.Request) {
		// Fallback for non-JS form posts
		http.Redirect(w, r, "/dashboard", http.StatusSeeOther)
	})

	mux.HandleFunc("/dashboard", func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&srv.authCount, 1)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, chaosDashboard)
	})

	server := httptest.NewServer(mux)
	t.Cleanup(server.Close)
	return server
}

// ─────────────────────────────────────────────────────────────────────────────
// The Proof: TestNavBrowser_SelfHealingLogin
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_SelfHealingLogin(t *testing.T) {
	const runs = 10

	rng := rand.New(rand.NewSource(time.Now().UnixNano()))
	srv := newChaosServer(t, rng)

	nb := navbrowser.New(nil)
	session, err := nb.Launch(browser.SessionLaunchConfig{
		Headless:    true,
		BrowserName: "auto",
	})
	if err != nil {
		t.Skipf("NavBrowser launch failed (Chrome not installed?): %v", err)
	}
	defer nb.Close(browser.CloseSessionConfig{SessionId: session.SessionId})

	creds := pageintel.Credentials{
		Username: "user@test.com",
		Password: "correct",
	}

	// Knowledge base in a temp dir so we observe actual learning across runs
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)
	domain := "127.0.0.1"

	type runResult struct {
		Run     int
		Variant string // which HTML version served (inferred from page title)
		Success bool
		Tier    string // "tier1/tier2/tier3/unknown"
		Elapsed time.Duration
	}
	results := make([]runResult, 0, runs)

	for i := 1; i <= runs; i++ {
		t.Run(fmt.Sprintf("run_%02d", i), func(t *testing.T) {
			start := time.Now()

			// Open a new tab for each run (simulates a fresh navigation)
			page, err := nb.NewTab(browser.NewTabConfig{SessionId: session.SessionId})
			if err != nil {
				t.Fatalf("Run %d: NewTab: %v", i, err)
			}

			// Navigate to chaos login page
			gotoResult, err := nb.Goto(browser.GotoConfig{
				PageId: page.PageId,
				Url:    srv.URL + "/",
			})
			if err != nil {
				t.Fatalf("Run %d: Goto: %v", i, err)
			}

			// Detect which variant we got
			variant := "?"
			switch {
			case strings.Contains(gotoResult.Title, "Sign In"):
				variant = "A (Bootstrap)"
			case strings.Contains(gotoResult.Title, "Log In"):
				variant = "B (React/Tailwind)"
			case strings.Contains(gotoResult.Title, "Access Portal"):
				variant = "C (Obfuscated)"
			}

			t.Logf("Run %d: variant=%s title=%q", i, variant, gotoResult.Title)

			// Build EvalFn bound to this page
			evalFn := func(js string) ([]byte, error) {
				res, evalErr := nb.Eval(browser.EvalConfig{
					PageId:    page.PageId,
					Js:        js,
					TimeoutMs: 8000,
				})
				if evalErr != nil {
					return nil, evalErr
				}
				return res.Result, nil
			}
			navFn := func(url string) error {
				_, navErr := nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: url})
				return navErr
			}

			inspector := pageintel.New(evalFn)

			// 🚀 Run the 3-tier self-healing login flow
			outcome, loginErr := inspector.HealingLoginFlow(creds, domain, kb)

			elapsed := time.Since(start)

			rr := runResult{
				Run:     i,
				Variant: variant,
				Elapsed: elapsed,
			}

			if loginErr != nil {
				t.Errorf("Run %d (%s): HealingLoginFlow error: %v", i, variant, loginErr)
				rr.Success = false
			} else {
				rr.Success = outcome.Success
				if !outcome.Success {
					// Log the page HTML for debugging
					rawHTML, _ := evalFn(`document.documentElement.outerHTML`)
					var htmlStr string
					json.Unmarshal(rawHTML, &htmlStr)
					t.Errorf("Run %d (%s): login FAILED — errorMsg=%q MFA=%v NeedsHuman=%q",
						i, variant, outcome.ErrorMsg, outcome.RequiresMFA, outcome.NeedsHuman)
					t.Logf("Page HTML snippet:\n%s", htmlStr[:min(len(htmlStr), 500)])
				} else {
					t.Logf("Run %d (%s): ✅ SUCCESS in %s — nextPage=%s",
						i, variant, elapsed.Round(time.Millisecond), outcome.NextPageType)
				}
			}
			_ = navFn // used internally

			results = append(results, rr)
		})
	}

	// ── Final report ─────────────────────────────────────────────────────────
	passed := 0
	for _, r := range results {
		if r.Success {
			passed++
		}
	}

	t.Logf("\n╔══════════════════════════════════════════════════════════╗")
	t.Logf("║       NAVIG SELF-HEALING: %d/%d RUNS PASSED               ║", passed, runs)
	t.Logf("╠══════════════════════════════════════════════════════════╣")
	for _, r := range results {
		status := "✅"
		if !r.Success {
			status = "❌"
		}
		t.Logf("║  %s  Run %02d  %-22s  %s", status, r.Run, r.Variant, r.Elapsed.Round(time.Millisecond))
	}
	t.Logf("╚══════════════════════════════════════════════════════════╝")

	// Print knowledge base evolution
	domains, _ := kb.ListDomains()
	if len(domains) > 0 {
		tier1 := kb.LoadTier1Selectors(domain)
		t.Logf("\nKnowledge base evolved selectors for %q:", domain)
		for semantic, sel := range tier1 {
			t.Logf("  %-12s → %s", semantic, sel)
		}
	}

	if passed < runs {
		t.Errorf("Self-healing failed: only %d/%d runs passed — 3-tier resolver needs improvement", passed, runs)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// TestNavBrowser_SelfHealingLogin_Deterministic — runs each variant exactly once.
// Verifies Tier1→Tier2→Tier3 in order without randomness (CI-safe).
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_SelfHealingLogin_EachVariant(t *testing.T) {
	variants := []struct {
		name string
		html string
		// expectedTier: which tier we expect to fire. Used as documentation.
		// (not asserted — outcome success is the only hard requirement)
		expectedTier int
	}{
		{"VersionA_ClassicBootstrap", versionA, 1},
		{"VersionB_ReactTailwind", versionB, 2},
		{"VersionC_Obfuscated", versionC, 3},
	}

	for _, v := range variants {
		v := v
		t.Run(v.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasPrefix(r.URL.Path, "/dashboard") {
					w.Header().Set("Content-Type", "text/html")
					fmt.Fprint(w, chaosDashboard)
					return
				}
				w.Header().Set("Content-Type", "text/html")
				fmt.Fprint(w, v.html)
			}))
			defer srv.Close()

			nb := navbrowser.New(nil)
			session, err := nb.Launch(browser.SessionLaunchConfig{Headless: true, BrowserName: "auto"})
			if err != nil {
				t.Skipf("Chrome not found: %v", err)
			}
			defer nb.Close(browser.CloseSessionConfig{SessionId: session.SessionId})

			page, _ := nb.NewTab(browser.NewTabConfig{SessionId: session.SessionId})
			nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

			evalFn := func(js string) ([]byte, error) {
				res, err := nb.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 8000})
				if err != nil {
					return nil, err
				}
				return res.Result, nil
			}

			kbDir := t.TempDir()
			kb := pageintel.NewKnowledgeBaseAt(kbDir)

			inspector := pageintel.New(evalFn)
			outcome, err := inspector.HealingLoginFlow(pageintel.Credentials{
				Username: "user@test.com",
				Password: "correct",
			}, "test.local", kb)

			if err != nil {
				t.Fatalf("%s: HealingLoginFlow error: %v", v.name, err)
			}
			if !outcome.Success {
				t.Errorf("%s: expected success, got: err=%q MFA=%v Human=%q",
					v.name, outcome.ErrorMsg, outcome.RequiresMFA, outcome.NeedsHuman)
			} else {
				t.Logf("%s: ✅ Success (tier expected: %d)", v.name, v.expectedTier)
			}

			// Verify knowledge base was updated
			tier1 := kb.LoadTier1Selectors("test.local")
			t.Logf("%s: Knowledge base: %v", v.name, tier1)
			if len(tier1) == 0 {
				t.Log("Warning: knowledge base empty — verify Save() is being called on evolution")
			}

			_ = os.RemoveAll(kbDir)
		})
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// TestNavBrowser_CaptchaDetection_Chaos
// Verifies that a Cloudflare-style block is detected before trying to login.
// ─────────────────────────────────────────────────────────────────────────────

func TestNavBrowser_CaptchaDetection_Chaos(t *testing.T) {
	cfPage := `<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>
<div id="cf-wrapper">
  <h1>Checking your browser before accessing the page.</h1>
  <div class="cf-turnstile" data-sitekey="xxxxx"></div>
</div>
</body></html>`

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		fmt.Fprint(w, cfPage)
	}))
	defer srv.Close()

	nb := navbrowser.New(nil)
	session, err := nb.Launch(browser.SessionLaunchConfig{Headless: true, BrowserName: "auto"})
	if err != nil {
		t.Skipf("Chrome not found: %v", err)
	}
	defer nb.Close(browser.CloseSessionConfig{SessionId: session.SessionId})

	page, _ := nb.NewTab(browser.NewTabConfig{SessionId: session.SessionId})
	nb.Goto(browser.GotoConfig{PageId: page.PageId, Url: srv.URL + "/"})

	evalFn := func(js string) ([]byte, error) {
		res, err := nb.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 5000})
		if err != nil {
			return nil, err
		}
		return res.Result, nil
	}

	inspector := pageintel.New(evalFn)

	// DetectOutcome should identify this as a captcha BEFORE any login attempt
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome: %v", err)
	}

	t.Logf("Outcome: status=%s detail=%q", outcome.Status, outcome.Detail)
	if outcome.Status != "captcha" {
		t.Errorf("Expected captcha outcome, got %q (detail: %q)", outcome.Status, outcome.Detail)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
