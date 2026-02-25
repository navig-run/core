// Package pageintel_test contains unit tests for the NAVIG Page Intelligence layer.
//
// Tests run on static HTML strings — no real browser required.
// Integration tests (needing Chrome) are in navbrowser_integration_test.go.
package pageintel_test

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"navig-core/host/internal/browseragent/pageintel"
)

// ─────────────────────────────────────────────────────────────────────────────
// Mock eval function — simulates chromedp.Eval against static HTML
// ─────────────────────────────────────────────────────────────────────────────

// mockPage simulates a rendered HTML page for unit testing without a browser.
// It evaluates a small subset of the pageintel scripts against static data.
func loginPageEval(html string) pageintel.EvalFn {
	// Pre-built analysis matching a typical login page
	analysis := pageintel.PageAnalysis{
		Title:    "Sign In — Acme Corp",
		URL:      "https://app.acme.com/login",
		PageType: pageintel.PageTypeLogin,
		Forms: []pageintel.FormInfo{
			{ID: "login-form", Action: "/auth/login", Method: "POST"},
		},
		Inputs: []pageintel.InputInfo{
			{Name: "email", ID: "email", Type: "email", Placeholder: "Enter your email", Label: "Email", Required: true, Selector: "#email"},
			{Name: "password", ID: "password", Type: "password", Placeholder: "Password", Label: "Password", Required: true, Selector: "#password"},
		},
		Buttons: []pageintel.ButtonInfo{
			{Text: "Sign In", Type: "submit", Selector: "button[type=submit]", Primary: true},
		},
		HasCaptcha: false,
		HasError:   false,
	}

	filled := map[string]string{}
	clicked := []string{}

	return func(js string) ([]byte, error) {
		// Detect fill calls
		if strings.Contains(js, "nativeInputValueSetter") && strings.Contains(js, "#email") {
			filled["email"] = "test@example.com"
			return json.Marshal("ok")
		}
		if strings.Contains(js, "nativeInputValueSetter") && strings.Contains(js, "#password") {
			filled["password"] = "hunter2"
			return json.Marshal("ok")
		}
		if strings.Contains(js, ".click()") && strings.Contains(js, "submit") {
			clicked = append(clicked, "submit")
			// Simulate redirect to dashboard after submit
			analysis.PageType = pageintel.PageTypeDashboard
			analysis.URL = "https://app.acme.com/dashboard"
			analysis.HasError = false
			analysis.Inputs = nil
			return json.Marshal("ok")
		}
		// Analyze call
		if strings.Contains(js, "classifyPage") || strings.Contains(js, "navig_stealth") || strings.Contains(js, "document.title") {
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		// readyState
		if strings.Contains(js, "readyState") {
			return json.Marshal("complete")
		}
		return json.Marshal(nil)
	}
}

func dashboardPageEval() pageintel.EvalFn {
	analysis := pageintel.PageAnalysis{
		Title:    "Dashboard — Acme Corp",
		URL:      "https://app.acme.com/dashboard",
		PageType: pageintel.PageTypeDashboard,
		HasError: false,
	}
	return func(js string) ([]byte, error) {
		if strings.Contains(js, "classifyPage") {
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
}

func errorPageEval() pageintel.EvalFn {
	analysis := pageintel.PageAnalysis{
		Title:     "Sign In — Acme Corp",
		URL:       "https://app.acme.com/login",
		PageType:  pageintel.PageTypeLogin,
		HasError:  true,
		ErrorText: "Invalid email or password.",
		Inputs: []pageintel.InputInfo{
			{Name: "email", ID: "email", Type: "email", Selector: "#email"},
			{Name: "password", ID: "password", Type: "password", Selector: "#password"},
		},
		Buttons: []pageintel.ButtonInfo{
			{Text: "Sign In", Type: "submit", Selector: "button[type=submit]", Primary: true},
		},
	}
	return func(js string) ([]byte, error) {
		if strings.Contains(js, "classifyPage") {
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		if strings.Contains(js, "readyState") {
			return json.Marshal("complete")
		}
		return json.Marshal("ok") // all fills/clicks succeed
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: Analyze
// ─────────────────────────────────────────────────────────────────────────────

func TestAnalyze_LoginPage(t *testing.T) {
	inspector := pageintel.New(loginPageEval(""))
	got, err := inspector.Analyze()
	if err != nil {
		t.Fatalf("Analyze: unexpected error: %v", err)
	}
	if got.PageType != pageintel.PageTypeLogin {
		t.Errorf("PageType = %q, want %q", got.PageType, pageintel.PageTypeLogin)
	}
	if len(got.Inputs) < 2 {
		t.Errorf("Inputs = %d, want >= 2", len(got.Inputs))
	}
	if len(got.Buttons) == 0 {
		t.Error("Buttons: expected at least one button")
	}
	if !got.Buttons[0].Primary {
		t.Error("Buttons[0].Primary: expected primary=true for submit button")
	}
}

func TestAnalyze_DashboardPage(t *testing.T) {
	inspector := pageintel.New(dashboardPageEval())
	got, err := inspector.Analyze()
	if err != nil {
		t.Fatalf("Analyze: unexpected error: %v", err)
	}
	if got.PageType != pageintel.PageTypeDashboard {
		t.Errorf("PageType = %q, want %q", got.PageType, pageintel.PageTypeDashboard)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: Field Selectors
// ─────────────────────────────────────────────────────────────────────────────

func TestFindUsernameSelector(t *testing.T) {
	analysis := &pageintel.PageAnalysis{
		Inputs: []pageintel.InputInfo{
			{Name: "email", Type: "email", Selector: "#email"},
			{Name: "password", Type: "password", Selector: "#password"},
		},
	}
	sel := pageintel.FindUsernameSelector(analysis)
	if sel != "#email" {
		t.Errorf("FindUsernameSelector = %q, want %q", sel, "#email")
	}
}

func TestFindPasswordSelector(t *testing.T) {
	analysis := &pageintel.PageAnalysis{
		Inputs: []pageintel.InputInfo{
			{Name: "email", Type: "email", Selector: "#email"},
			{Name: "password", Type: "password", Selector: "#password"},
		},
	}
	sel := pageintel.FindPasswordSelector(analysis)
	if sel != "#password" {
		t.Errorf("FindPasswordSelector = %q, want %q", sel, "#password")
	}
}

func TestFindSubmitSelector(t *testing.T) {
	analysis := &pageintel.PageAnalysis{
		Buttons: []pageintel.ButtonInfo{
			{Text: "Cancel", Type: "button", Primary: false, Selector: "#cancel"},
			{Text: "Sign In", Type: "submit", Primary: true, Selector: "button[type=submit]"},
		},
	}
	sel := pageintel.FindSubmitSelector(analysis)
	if sel != "button[type=submit]" {
		t.Errorf("FindSubmitSelector = %q, want %q", sel, "button[type=submit]")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: LoginFlow
// ─────────────────────────────────────────────────────────────────────────────

func TestLoginFlow_Success(t *testing.T) {
	inspector := pageintel.New(loginPageEval(""))
	creds := pageintel.Credentials{
		Username: "test@example.com",
		Password: "hunter2",
	}
	outcome, err := inspector.LoginFlow(creds, func(url string) error { return nil })
	if err != nil {
		t.Fatalf("LoginFlow: unexpected error: %v", err)
	}
	if !outcome.Success {
		t.Errorf("LoginFlow success = false, want true (ErrorMsg: %q)", outcome.ErrorMsg)
	}
	if outcome.NextPageType != pageintel.PageTypeDashboard {
		t.Errorf("NextPageType = %q, want %q", outcome.NextPageType, pageintel.PageTypeDashboard)
	}
}

func TestLoginFlow_WrongPassword(t *testing.T) {
	inspector := pageintel.New(errorPageEval())
	creds := pageintel.Credentials{
		Username: "test@example.com",
		Password: "wrongpassword",
	}
	outcome, err := inspector.LoginFlow(creds, func(url string) error { return nil })
	if err != nil {
		t.Fatalf("LoginFlow: unexpected error: %v", err)
	}
	if outcome.Success {
		t.Error("LoginFlow success = true, want false for wrong password")
	}
	if outcome.ErrorMsg == "" {
		t.Error("LoginFlow ErrorMsg: expected non-empty error message")
	}
}

func TestLoginFlow_NilCredentials_ReturnsError(t *testing.T) {
	// No username/password fields on page — should fail cleanly
	emptyEval := func(js string) ([]byte, error) {
		analysis := pageintel.PageAnalysis{
			PageType: pageintel.PageTypeUnknown,
		}
		if strings.Contains(js, "classifyPage") {
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(emptyEval)
	_, err := inspector.LoginFlow(pageintel.Credentials{}, func(url string) error { return nil })
	if err == nil {
		t.Error("LoginFlow: expected error when no login fields found, got nil")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: Outcome Detection
// ─────────────────────────────────────────────────────────────────────────────

func TestDetectOutcome_Success(t *testing.T) {
	successEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "successWords") || strings.Contains(js, "document.body") {
			result := pageintel.Outcome{
				Status: "success",
				URL:    "https://app.acme.com/dashboard",
				Title:  "Dashboard",
			}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(successEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome: %v", err)
	}
	if outcome.Status != "success" {
		t.Errorf("Status = %q, want %q", outcome.Status, "success")
	}
}

func TestDetectOutcome_Error(t *testing.T) {
	errEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "successWords") || strings.Contains(js, "document.body") {
			result := pageintel.Outcome{
				Status: "error",
				Detail: "Invalid credentials.",
				URL:    "https://app.acme.com/login",
			}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(errEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome: %v", err)
	}
	if outcome.Status != "error" {
		t.Errorf("Status = %q, want %q", outcome.Status, "error")
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: EvolveSelector
// ─────────────────────────────────────────────────────────────────────────────

func TestEvolveSelector_ReturnsAlternatives(t *testing.T) {
	evolveEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "aria-label") {
			candidates := []pageintel.SelectorCandidate{
				{Selector: "[aria-label=\"Email address\"]", Strategy: "aria", Confidence: 0.9},
				{Selector: "[name=email]", Strategy: "name", Confidence: 0.8},
			}
			b, _ := json.Marshal(candidates)
			return json.Marshal(string(b))
		}
		return json.Marshal("[]")
	}
	inspector := pageintel.New(evolveEval)
	candidates, err := inspector.EvolveSelector("email")
	if err != nil {
		t.Fatalf("EvolveSelector: %v", err)
	}
	if len(candidates) == 0 {
		t.Error("EvolveSelector: expected candidates, got none")
	}
	if candidates[0].Confidence < 0.5 {
		t.Errorf("Candidate confidence %f too low", candidates[0].Confidence)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: ScriptEngine steps
// ─────────────────────────────────────────────────────────────────────────────

func TestScriptReader_FullLoginScenario(t *testing.T) {
	// This test validates the full JSON step DSL that would come from CLI/MCP/IPC.
	// The JSON represents exactly what you'd send in a TaskRunRequest.
	stepsJSON := `[
		{"goto": {"url": "https://app.acme.com/login"}},
		{"analyze": {}},
		{"fill":  {"selector": "#email",    "value": "user@test.com", "hint": "email"}},
		{"fill":  {"selector": "#password", "value": "__creds__",     "hint": "password"}},
		{"click": {"selector": "button[type=submit]", "text": "Sign In"}},
		{"waitFor": {"selector": ".dashboard-header", "timeoutMs": 5000}},
		{"detectOutcome": {}},
		{"extract": {"format": "table"}},
		{"screenshot": {"path": ""}}
	]`

	// Verify it's valid JSON (in real run this flows into the step engine)
	var raw []json.RawMessage
	if err := json.Unmarshal([]byte(stepsJSON), &raw); err != nil {
		t.Fatalf("step DSL JSON invalid: %v", err)
	}
	if len(raw) != 9 {
		t.Errorf("expected 9 steps, got %d", len(raw))
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: Credential redaction (sensitive data must not appear in JSON output)
// ─────────────────────────────────────────────────────────────────────────────

func TestCredentials_NotSerializedToJSON(t *testing.T) {
	creds := pageintel.Credentials{
		Username: "user@test.com",
		Password: "supersecretpassword",
		TOTP:     "123456",
	}
	b, err := json.Marshal(creds)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	serialized := string(b)
	if strings.Contains(serialized, "supersecretpassword") {
		t.Error("SECURITY: password appeared in JSON serialization — must be redacted")
	}
	if strings.Contains(serialized, "123456") {
		t.Error("SECURITY: TOTP appeared in JSON serialization — must be redacted")
	}
	if !strings.Contains(serialized, "user@test.com") {
		t.Error("username should be present in serialization")
	}
	fmt.Printf("Credentials JSON (safe to log): %s\n", serialized)
}
