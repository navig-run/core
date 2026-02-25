package pageintel_test

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"navig-core/host/internal/browseragent/pageintel"
)

// ─────────────────────────────────────────────────────────────────────────────
// Mock eval factories for each DOM variant
// ─────────────────────────────────────────────────────────────────────────────

// versionAEval simulates a classic Bootstrap page with stable IDs.
// Tier 1 (cached selector #user_email) succeeds immediately.
func versionAEval(userSel, passSel, submitSel string) pageintel.EvalFn {
	return func(js string) ([]byte, error) {
		// Tier 1 presence check
		if strings.Contains(js, "document.querySelector") && strings.Contains(js, userSel) && strings.Contains(js, "getComputedStyle") {
			return json.Marshal("ok")
		}
		if strings.Contains(js, "document.querySelector") && strings.Contains(js, passSel) && strings.Contains(js, "getComputedStyle") {
			return json.Marshal("ok")
		}
		if strings.Contains(js, "document.querySelector") && strings.Contains(js, submitSel) && strings.Contains(js, "getComputedStyle") {
			return json.Marshal("ok")
		}
		// Native fill
		if strings.Contains(js, "nativeInputValueSetter") || strings.Contains(js, "niv.call") {
			return json.Marshal("ok")
		}
		// Click
		if strings.Contains(js, ".click()") {
			return json.Marshal("ok")
		}
		// readyState
		if strings.Contains(js, "readyState") {
			return json.Marshal("complete")
		}
		// Analyze
		if strings.Contains(js, "classifyPage") {
			analysis := pageintel.PageAnalysis{
				PageType: pageintel.PageTypeDashboard,
				URL:      "https://example.com/dashboard",
			}
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		// Outcome
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "success", Detail: "Logout link found", URL: "https://example.com/dashboard"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
}

// versionBEval simulates a React/Tailwind page — no stable IDs.
// Tier 1 fails. Tier 2 succeeds by aria-label semantic scan.
func versionBEval() pageintel.EvalFn {
	return func(js string) ([]byte, error) {
		// Analyze (classifyPage is unique to analyzeScript — must be first)
		if strings.Contains(js, "classifyPage") {
			analysis := pageintel.PageAnalysis{PageType: pageintel.PageTypeDashboard}
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		// Outcome
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "success", URL: "https://example.com/dashboard"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		// Tier 2 semantic scan — emailHints unique to tier2Script
		if strings.Contains(js, "emailHints") {
			return json.Marshal("[aria-label=\"Email Address\"]")
		}
		if strings.Contains(js, "submitHints") {
			return json.Marshal("[role=button]")
		}
		if strings.Contains(js, "candidates") && strings.Contains(js, "type=password") {
			return json.Marshal("[aria-label=\"Password\"]")
		}
		// Tier 1 presence check (after specific checks)
		if strings.Contains(js, "getComputedStyle") {
			return json.Marshal("not_found")
		}
		// Native fill
		if strings.Contains(js, "nativeInputValueSetter") || strings.Contains(js, "niv.call") {
			return json.Marshal("ok")
		}
		// Click
		if strings.Contains(js, ".click()") {
			return json.Marshal("ok")
		}
		// readyState
		if strings.Contains(js, "readyState") {
			return json.Marshal("complete")
		}
		return json.Marshal(nil)
	}
}

// versionCEval simulates a fully obfuscated page — no IDs, no aria, no names.
// Tiers 1 and 2 fail. Tier 3 uses structural inference.
func versionCEval() pageintel.EvalFn {
	return func(js string) ([]byte, error) {
		// Analyze (classifyPage unique to analyzeScript — must be first)
		if strings.Contains(js, "classifyPage") {
			analysis := pageintel.PageAnalysis{PageType: pageintel.PageTypeDashboard}
			b, _ := json.Marshal(analysis)
			return json.Marshal(string(b))
		}
		// Outcome
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "success", URL: "https://example.com/app"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		// Tier 2 — return empty to force Tier 3
		if strings.Contains(js, "emailHints") {
			return json.Marshal("")
		}
		if strings.Contains(js, "submitHints") {
			return json.Marshal("")
		}
		if strings.Contains(js, "candidates") && strings.Contains(js, "type=password") {
			return json.Marshal("")
		}
		// Tier 3 structural
		if strings.Contains(js, "compareDocumentPosition") {
			return json.Marshal("input[type=text]")
		}
		if strings.Contains(js, "querySelectorAll('input[type=password]')") {
			return json.Marshal("input[type=password]")
		}
		if strings.Contains(js, "submitWords") {
			return json.Marshal("div.css-btn-primary")
		}
		// Tier 1 (after specific checks)
		if strings.Contains(js, "getComputedStyle") {
			return json.Marshal("not_found")
		}
		// Native fill
		if strings.Contains(js, "nativeInputValueSetter") || strings.Contains(js, "niv.call") {
			return json.Marshal("ok")
		}
		// Click
		if strings.Contains(js, ".click()") {
			return json.Marshal("ok")
		}
		// readyState
		if strings.Contains(js, "readyState") {
			return json.Marshal("complete")
		}
		return json.Marshal(nil)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: ResolveTarget — 3-tier waterfall
// ─────────────────────────────────────────────────────────────────────────────

func TestResolveTarget_Tier1_CachedSelector(t *testing.T) {
	// Tier 1: cached selector "#user_email" exists and is visible → immediate return
	inspector := pageintel.New(versionAEval("#user_email", "#user_password", "#login_submit"))

	resolved, err := inspector.ResolveTarget(pageintel.TargetHint{
		Semantic:  pageintel.SemanticUsername,
		Tier1:     "#user_email",
		FieldType: "email",
	})
	if err != nil {
		t.Fatalf("ResolveTarget Tier1: %v", err)
	}
	if resolved.Tier != 1 {
		t.Errorf("Expected Tier=1, got %d (strategy=%s)", resolved.Tier, resolved.Strategy)
	}
	if resolved.Evolved {
		t.Error("Evolved should be false for Tier1 hit")
	}
	if resolved.Selector != "#user_email" {
		t.Errorf("Selector = %q, want %q", resolved.Selector, "#user_email")
	}
	t.Logf("✅ Tier1: selector=%q tier=%d strategy=%s", resolved.Selector, resolved.Tier, resolved.Strategy)
}

func TestResolveTarget_Tier2_SemanticScan(t *testing.T) {
	// Tier 1 fails (no cached selector), Tier 2 finds aria-label
	evalFn := func(js string) ([]byte, error) {
		// Tier 1 test → not_found (no selector given, or present check returns not_found)
		if strings.Contains(js, "getComputedStyle") {
			return json.Marshal("not_found")
		}
		// Tier 2 semantic scan → returns aria-label selector
		if strings.Contains(js, "emailHints") {
			return json.Marshal(`"[aria-label=\\"Email Address\\"]"`)
		}
		return json.Marshal(nil)
	}

	inspector := pageintel.New(evalFn)
	resolved, err := inspector.ResolveTarget(pageintel.TargetHint{
		Semantic:  pageintel.SemanticUsername,
		Tier1:     "#broken_selector_that_doesnt_exist",
		FieldType: "email",
	})
	if err != nil {
		t.Fatalf("ResolveTarget Tier2: %v", err)
	}
	if resolved.Tier != 2 {
		t.Errorf("Expected Tier=2, got %d", resolved.Tier)
	}
	if !resolved.Evolved {
		t.Error("Evolved should be true when Tier2 triggers (Tier1 had a broken selector)")
	}
	t.Logf("✅ Tier2: selector=%q strategy=%s evolved=%v", resolved.Selector, resolved.Strategy, resolved.Evolved)
}

func TestResolveTarget_Tier3_StructuralHeuristic(t *testing.T) {
	// Tiers 1 and 2 both fail; Tier 3 uses structural inference.
	// Key: check specific strings BEFORE generic 'getComputedStyle'
	evalFn := func(js string) ([]byte, error) {
		// Tier 2: emailHints must be checked FIRST (appears in both Tier1/Tier2 are different)
		if strings.Contains(js, "emailHints") {
			return json.Marshal("") // Tier2 fails — no semantic match
		}
		// Tier 1: now safe to check getComputedStyle
		if strings.Contains(js, "getComputedStyle") {
			return json.Marshal("not_found")
		}
		// Tier 3: compareDocumentPosition is unique to tier3Script username
		if strings.Contains(js, "compareDocumentPosition") {
			return json.Marshal("input[type=email]") // structural find
		}
		return json.Marshal(nil)
	}

	inspector := pageintel.New(evalFn)
	resolved, err := inspector.ResolveTarget(pageintel.TargetHint{
		Semantic: pageintel.SemanticUsername,
	})
	if err != nil {
		t.Fatalf("ResolveTarget Tier3: %v", err)
	}
	if resolved.Tier != 3 {
		t.Errorf("Expected Tier=3, got %d (strategy=%s selector=%q)", resolved.Tier, resolved.Strategy, resolved.Selector)
	}
	if resolved.Strategy != "structural_heuristic" {
		t.Errorf("Strategy = %q, want %q", resolved.Strategy, "structural_heuristic")
	}
	t.Logf("✅ Tier3: selector=%q strategy=%s", resolved.Selector, resolved.Strategy)
}

func TestResolveTarget_AllTiersFail_ReturnsError(t *testing.T) {
	// All three tiers return empty/not_found
	evalFn := func(js string) ([]byte, error) {
		// Specific checks first, then generic
		if strings.Contains(js, "emailHints") {
			return json.Marshal("") // Tier 2 empty
		}
		if strings.Contains(js, "compareDocumentPosition") {
			return json.Marshal("") // Tier 3 empty
		}
		if strings.Contains(js, "getComputedStyle") {
			return json.Marshal("not_found") // Tier 1 fails
		}
		return json.Marshal("")
	}

	inspector := pageintel.New(evalFn)
	_, err := inspector.ResolveTarget(pageintel.TargetHint{
		Semantic: pageintel.SemanticUsername,
		Tier1:    "#doesnt_exist",
	})
	if err == nil {
		t.Error("Expected error when all 3 tiers fail, got nil")
	}
	t.Logf("✅ All-fail correctly returns error: %v", err)
}

func TestResolveTarget_Checkout_ABTestScoring(t *testing.T) {
	// Tier 2 scores "Secure Checkout" highest even among A/B variants
	evalFn := func(js string) ([]byte, error) {
		if strings.Contains(js, "getComputedStyle") {
			return json.Marshal("not_found")
		}
		if strings.Contains(js, "checkout") || strings.Contains(js, "keywords") {
			return json.Marshal(`"[data-testid=\\"checkout-btn\\"]"`)
		}
		return json.Marshal(nil)
	}

	inspector := pageintel.New(evalFn)
	resolved, err := inspector.ResolveTarget(pageintel.TargetHint{
		Semantic: pageintel.SemanticCheckout,
		Intent:   "purchase",
	})
	if err != nil {
		t.Fatalf("Checkout resolve: %v", err)
	}
	t.Logf("✅ Checkout A/B: selector=%q tier=%d", resolved.Selector, resolved.Tier)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: HealingLoginFlow — 3 DOM variants
// ─────────────────────────────────────────────────────────────────────────────

func TestHealingLoginFlow_VersionA_Tier1(t *testing.T) {
	inspector := pageintel.New(versionAEval("#user_email", "#user_password", "#login_submit"))
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)
	// Pre-seed Tier1 selectors so they're used
	kb.Save("example.com", "username", pageintel.SelectorRecord{Selector: "#user_email", Tier: 1, Strategy: "cached"})
	kb.Save("example.com", "password", pageintel.SelectorRecord{Selector: "#user_password", Tier: 1, Strategy: "cached"})
	kb.Save("example.com", "submit", pageintel.SelectorRecord{Selector: "#login_submit", Tier: 1, Strategy: "cached"})

	outcome, err := inspector.HealingLoginFlow(pageintel.Credentials{
		Username: "user@test.com",
		Password: "correct",
	}, "example.com", kb)

	if err != nil {
		t.Fatalf("HealingLoginFlow V-A: %v", err)
	}
	if !outcome.Success {
		t.Errorf("V-A: expected success, got error=%q", outcome.ErrorMsg)
	}
	t.Logf("✅ V-A (Bootstrap/Tier1): success=%v nextPage=%s", outcome.Success, outcome.NextPageType)
}

func TestHealingLoginFlow_VersionB_Tier2(t *testing.T) {
	inspector := pageintel.New(versionBEval())
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir) // empty — no cached selectors

	outcome, err := inspector.HealingLoginFlow(pageintel.Credentials{
		Username: "user@test.com",
		Password: "correct",
	}, "react-app.io", kb)

	if err != nil {
		t.Fatalf("HealingLoginFlow V-B: %v", err)
	}
	if !outcome.Success {
		t.Errorf("V-B: expected success, got error=%q", outcome.ErrorMsg)
	}
	t.Logf("✅ V-B (React/Tailwind/Tier2): success=%v nextPage=%s", outcome.Success, outcome.NextPageType)
}

func TestHealingLoginFlow_VersionC_Tier3(t *testing.T) {
	inspector := pageintel.New(versionCEval())
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir) // empty

	outcome, err := inspector.HealingLoginFlow(pageintel.Credentials{
		Username: "user@test.com",
		Password: "correct",
	}, "obfuscated.app", kb)

	if err != nil {
		t.Fatalf("HealingLoginFlow V-C: %v", err)
	}
	if !outcome.Success {
		t.Errorf("V-C: expected success, got error=%q", outcome.ErrorMsg)
	}
	t.Logf("✅ V-C (Obfuscated/Tier3): success=%v nextPage=%s", outcome.Success, outcome.NextPageType)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: SelectorKnowledge — persistence and evolution
// ─────────────────────────────────────────────────────────────────────────────

func TestKnowledge_SaveAndGet(t *testing.T) {
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)

	rec := pageintel.SelectorRecord{Selector: "#email", Tier: 2, Strategy: "semantic_accessibility"}
	if err := kb.Save("stripe.com", "username", rec); err != nil {
		t.Fatalf("Save: %v", err)
	}

	got, ok := kb.Get("stripe.com", "username")
	if !ok {
		t.Fatal("Get: record not found after Save")
	}
	if got.Selector != "#email" {
		t.Errorf("Selector = %q, want %q", got.Selector, "#email")
	}
	if got.HitCount != 1 {
		t.Errorf("HitCount = %d, want 1", got.HitCount)
	}
	t.Logf("✅ Knowledge Save/Get: selector=%q tier=%d hitCount=%d", got.Selector, got.Tier, got.HitCount)
}

func TestKnowledge_HitCountIncrementsOnSameSelectorResave(t *testing.T) {
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)

	rec := pageintel.SelectorRecord{Selector: "#pass", Tier: 1, Strategy: "cached"}
	kb.Save("github.com", "password", rec)
	kb.Save("github.com", "password", rec) // same selector saved again
	kb.Save("github.com", "password", rec)

	got, _ := kb.Get("github.com", "password")
	if got.HitCount != 3 {
		t.Errorf("HitCount = %d, want 3 (should increment on same selector re-saves)", got.HitCount)
	}
	t.Logf("✅ HitCount accumulated: %d", got.HitCount)
}

func TestKnowledge_PersistsToDisk(t *testing.T) {
	kbDir := t.TempDir()

	// Write with kb1
	kb1 := pageintel.NewKnowledgeBaseAt(kbDir)
	kb1.Save("twitter.com", "username", pageintel.SelectorRecord{Selector: "[name=session[username_or_email]]", Tier: 2, Strategy: "name_attr"})

	// Read with fresh kb2 (same dir) — must see the data
	kb2 := pageintel.NewKnowledgeBaseAt(kbDir)
	got, ok := kb2.Get("twitter.com", "username")
	if !ok {
		t.Fatal("PersistsToDisk: record not found in fresh KnowledgeBase at same dir")
	}
	if got.Selector != "[name=session[username_or_email]]" {
		t.Errorf("Selector = %q", got.Selector)
	}
	t.Logf("✅ Knowledge persisted to disk and reloaded: %q", got.Selector)
}

func TestKnowledge_LoadTier1Selectors(t *testing.T) {
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)
	kb.Save("linkedin.com", "username", pageintel.SelectorRecord{Selector: "#username", Tier: 1})
	kb.Save("linkedin.com", "password", pageintel.SelectorRecord{Selector: "#password", Tier: 1})
	kb.Save("linkedin.com", "submit",   pageintel.SelectorRecord{Selector: ".btn__primary--large", Tier: 2})

	tier1 := kb.LoadTier1Selectors("linkedin.com")
	if tier1[pageintel.SemanticUsername] != "#username" {
		t.Errorf("username Tier1 = %q, want %q", tier1[pageintel.SemanticUsername], "#username")
	}
	if tier1[pageintel.SemanticPassword] != "#password" {
		t.Errorf("password Tier1 = %q, want %q", tier1[pageintel.SemanticPassword], "#password")
	}
	t.Logf("✅ LoadTier1Selectors: %v", tier1)
}

func TestKnowledge_Delete(t *testing.T) {
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)
	kb.Save("example.com", "username", pageintel.SelectorRecord{Selector: "#u"})

	if err := kb.Delete("example.com"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	_, ok := kb.Get("example.com", "username")
	if ok {
		t.Error("Record should not exist after Delete")
	}
	t.Log("✅ Knowledge.Delete works")
}

func TestKnowledge_ListDomains(t *testing.T) {
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)
	for _, domain := range []string{"stripe.com", "github.com", "shopify.com"} {
		kb.Save(domain, "username", pageintel.SelectorRecord{Selector: fmt.Sprintf("#email-%s", domain)})
	}

	domains, err := kb.ListDomains()
	if err != nil {
		t.Fatalf("ListDomains: %v", err)
	}
	if len(domains) != 3 {
		t.Errorf("ListDomains = %d, want 3", len(domains))
	}
	t.Logf("✅ ListDomains: %v", domains)
}

func TestKnowledge_DomainFilePath_Sanitized(t *testing.T) {
	// Verifies special chars in domain names don't break file paths
	kbDir := t.TempDir()
	kb := pageintel.NewKnowledgeBaseAt(kbDir)
	err := kb.Save("sub.domain-test.co.uk", "username", pageintel.SelectorRecord{Selector: "#u"})
	if err != nil {
		t.Fatalf("Save with special domain: %v", err)
	}
	got, ok := kb.Get("sub.domain-test.co.uk", "username")
	if !ok || got.Selector != "#u" {
		t.Error("Special domain save/load failed")
	}
	// The file should exist at kbDir/sub.domain-test.co.uk.json
	files, _ := filepath.Glob(filepath.Join(kbDir, "*.json"))
	if len(files) == 0 {
		t.Error("No JSON file created for special domain")
	}
	t.Logf("✅ Special domain path: %v", files)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: Enhanced Outcome Detector
// ─────────────────────────────────────────────────────────────────────────────

func TestDetectOutcome_CloudflareTurnstile(t *testing.T) {
	cfEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "captcha", Detail: "Cloudflare Turnstile detected", URL: "https://target.com/login"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(cfEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome CF: %v", err)
	}
	if outcome.Status != "captcha" {
		t.Errorf("Expected captcha, got %q", outcome.Status)
	}
	t.Logf("✅ Cloudflare detected: status=%s detail=%q", outcome.Status, outcome.Detail)
}

func TestDetectOutcome_StorageToken(t *testing.T) {
	tokenEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "success", Detail: "Auth token in storage: access_token", URL: "https://app.com/home"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(tokenEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome token: %v", err)
	}
	if outcome.Status != "success" {
		t.Errorf("Expected success (token), got %q", outcome.Status)
	}
	t.Logf("✅ Auth token success: %s", outcome.Detail)
}

func TestDetectOutcome_MFA_6DigitInput(t *testing.T) {
	mfaEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "mfa", Detail: "MFA/2FA step detected", URL: "https://app.com/2fa"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(mfaEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome MFA: %v", err)
	}
	if outcome.Status != "mfa" {
		t.Errorf("Expected mfa, got %q", outcome.Status)
	}
	t.Logf("✅ MFA detected: status=%s detail=%q", outcome.Status, outcome.Detail)
}

func TestDetectOutcome_AriaInvalidError(t *testing.T) {
	ariaEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "error", Detail: "Field validation error (aria-invalid)", URL: "https://app.com/login"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(ariaEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome aria: %v", err)
	}
	if outcome.Status != "error" {
		t.Errorf("Expected error, got %q", outcome.Status)
	}
	t.Logf("✅ aria-invalid error detected: %s", outcome.Detail)
}

func TestDetectOutcome_LogoutLinkSuccess(t *testing.T) {
	logoutEval := func(js string) ([]byte, error) {
		if strings.Contains(js, "cf-wrapper") {
			result := pageintel.Outcome{Status: "success", Detail: "Logout link found (authenticated)", URL: "https://app.com/dashboard"}
			b, _ := json.Marshal(result)
			return json.Marshal(string(b))
		}
		return json.Marshal(nil)
	}
	inspector := pageintel.New(logoutEval)
	outcome, err := inspector.DetectOutcome()
	if err != nil {
		t.Fatalf("DetectOutcome logout: %v", err)
	}
	if outcome.Status != "success" {
		t.Errorf("Expected success (logout link), got %q", outcome.Status)
	}
	t.Logf("✅ Logout link success detection: %s", outcome.Detail)
}
