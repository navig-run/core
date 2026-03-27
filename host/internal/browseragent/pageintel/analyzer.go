// Package pageintel provides NAVIG Page Intelligence — DOM analysis,
// form detection, login flow automation, data extraction, and outcome detection.
//
// PageIntel is the reasoning layer that sits between raw CDP calls and
// the adaptive script engine. It makes the browser understand pages
// structurally rather than relying on hard-coded selectors.
//
// Core workflow:
//
//	page := pageintel.New(evalFn)
//	analysis, _ := page.Analyze()           // understand the page
//	result, _   := page.LoginFlow(creds)    // auto-login
//	data, _     := page.Extract("table")    // pull structured data
//	outcome, _  := page.DetectOutcome()     // did the action succeed?
package pageintel

import (
	"encoding/json"
	"fmt"
	"strings"
)

// ─────────────────────────────────────────────────────────────────────────────
// EvalFunc — abstraction over the CDP Eval call so pageintel is testable
// without a real browser.
// ─────────────────────────────────────────────────────────────────────────────

// EvalFn is a function that executes JavaScript in the live page context
// and returns the result as raw JSON bytes.
type EvalFn func(js string) ([]byte, error)

// ─────────────────────────────────────────────────────────────────────────────
// Page Analysis
// ─────────────────────────────────────────────────────────────────────────────

// PageType classifies the purpose of a page.
type PageType string

const (
	PageTypeLogin     PageType = "login"
	PageTypeDashboard PageType = "dashboard"
	PageTypeError     PageType = "error"
	PageTypeListing   PageType = "listing"
	PageTypeCaptcha   PageType = "captcha"
	PageTypeMFA       PageType = "mfa"
	PageTypeUnknown   PageType = "unknown"
)

// FormInfo describes an HTML form.
type FormInfo struct {
	ID     string `json:"id"`
	Action string `json:"action"`
	Method string `json:"method"`
}

// InputInfo describes an <input> element with enough context to fill it.
type InputInfo struct {
	Name        string `json:"name"`
	ID          string `json:"id"`
	Type        string `json:"type"`        // text, email, password, checkbox, hidden…
	Placeholder string `json:"placeholder"`
	Label       string `json:"label"`       // associated <label> text
	Required    bool   `json:"required"`
	Selector    string `json:"selector"`    // best CSS selector for this element
}

// ButtonInfo describes a clickable button.
type ButtonInfo struct {
	Text     string `json:"text"`
	Type     string `json:"type"` // submit, button, reset
	Selector string `json:"selector"`
	Primary  bool   `json:"primary"` // heuristic: is this the main action button?
}

// PageAnalysis is the structured output of Analyze().
type PageAnalysis struct {
	Title      string      `json:"title"`
	URL        string      `json:"url"`
	PageType   PageType    `json:"pageType"`
	Forms      []FormInfo  `json:"forms"`
	Inputs     []InputInfo `json:"inputs"`
	Buttons    []ButtonInfo `json:"buttons"`
	HasCaptcha bool        `json:"hasCaptcha"`
	HasError   bool        `json:"hasError"`
	ErrorText  string      `json:"errorText"`
	HasMFA     bool        `json:"hasMFA"`
}

// analyzeScript is a single-round-trip JS bundle that introspects the DOM
// and returns a PageAnalysis JSON. Running it as one eval avoids multiple
// CDP round-trips (each of which is ~10ms).
const analyzeScript = `(function() {
  function bestSelector(el) {
    if (el.id)   return '#' + el.id;
    if (el.name) return '[name=' + JSON.stringify(el.name) + ']';
    if (el.getAttribute('data-testid')) return '[data-testid=' + JSON.stringify(el.getAttribute('data-testid')) + ']';
    // Build nth-child path
    var path = [];
    var cur = el;
    while (cur && cur !== document.body) {
      var tag = cur.tagName.toLowerCase();
      var siblings = cur.parentNode ? Array.from(cur.parentNode.children).filter(c => c.tagName === cur.tagName) : [];
      if (siblings.length > 1) {
        tag += ':nth-of-type(' + (siblings.indexOf(cur) + 1) + ')';
      }
      path.unshift(tag);
      cur = cur.parentNode;
    }
    return path.join(' > ');
  }

  function labelFor(el) {
    if (el.id) {
      var lbl = document.querySelector('label[for=' + JSON.stringify(el.id) + ']');
      if (lbl) return lbl.innerText.trim();
    }
    var parent = el.closest('label');
    if (parent) return parent.innerText.replace(el.value || '', '').trim();
    // Aria
    var aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
    if (aria) {
      var ref = document.getElementById(aria);
      return ref ? ref.innerText.trim() : aria;
    }
    return '';
  }

  function classifyPage(title, body) {
    var t = (title + ' ' + body.substring(0, 500)).toLowerCase();
    if (/captcha|are you a robot|i am not a robot/.test(t)) return 'captcha';
    if (/two.factor|2fa|verification code|enter code|one-time|otp/.test(t)) return 'mfa';
    if (/sign in|log in|login|sign-in/.test(t)) return 'login';
    if (/error|404|403|not found|access denied|something went wrong/.test(t)) return 'error';
    if (/dashboard|home|welcome|overview|account/.test(t)) return 'dashboard';
    if (/results|listing|products|search|items|showing/.test(t)) return 'listing';
    return 'unknown';
  }

  function detectErrorText() {
    var selectors = [
      '.error', '.alert', '.alert-danger', '[role=alert]',
      '.error-message', '.form-error', '.notification-error',
      '.invalid-feedback', '[class*=error]', '[class*=danger]'
    ];
    for (var i = 0; i < selectors.length; i++) {
      var el = document.querySelector(selectors[i]);
      if (el && el.innerText.trim()) return el.innerText.trim().substring(0, 200);
    }
    return '';
  }

  var title   = document.title;
  var url     = window.location.href;
  var body    = document.body ? document.body.innerText : '';
  var errText = detectErrorText();

  // Forms
  var forms = Array.from(document.querySelectorAll('form')).map(f => ({
    id: f.id || '',
    action: f.action || '',
    method: (f.method || 'get').toUpperCase(),
  }));

  // Inputs (skip hidden, submit, button types)
  var skipTypes = new Set(['hidden', 'submit', 'button', 'reset', 'image', 'file']);
  var inputs = Array.from(document.querySelectorAll('input, textarea, select'))
    .filter(el => !skipTypes.has((el.type || 'text').toLowerCase()))
    .map(el => ({
      name:        el.name || '',
      id:          el.id || '',
      type:        el.type || (el.tagName.toLowerCase() === 'textarea' ? 'textarea' : 'select'),
      placeholder: el.placeholder || '',
      label:       labelFor(el),
      required:    el.required || false,
      selector:    bestSelector(el),
    }));

  // Buttons
  var buttons = Array.from(document.querySelectorAll('button, input[type=submit], [role=button], a.btn'))
    .filter(el => {
      var style = window.getComputedStyle(el);
      return style.display !== 'none' && style.visibility !== 'hidden';
    })
    .map((el, i) => {
      var text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
      var type = el.type || (el.tagName.toLowerCase() === 'a' ? 'link' : 'button');
      return {
        text:     text.substring(0, 80),
        type:     type,
        selector: bestSelector(el),
        primary:  type === 'submit' || /sign in|log in|continue|next|submit|login/i.test(text),
      };
    })
    .slice(0, 20);

  var bodyLower = body.toLowerCase();
  return JSON.stringify({
    title:      title,
    url:        url,
    pageType:   classifyPage(title, body),
    forms:      forms,
    inputs:     inputs,
    buttons:    buttons,
    hasCaptcha: /captcha|recaptcha|hcaptcha|are you a robot/.test(bodyLower),
    hasError:   errText.length > 0,
    errorText:  errText,
    hasMFA:     /two.factor|2fa|verification code|enter code|one-time password|otp/.test(bodyLower),
  });
})()`

// Inspector wraps an EvalFn and provides page intelligence methods.
type Inspector struct {
	eval EvalFn
}

// New creates a PageIntel Inspector bound to the provided eval function.
// Typically the eval function calls navbrowser.Eval on the active page.
func New(eval EvalFn) *Inspector {
	return &Inspector{eval: eval}
}

// Analyze performs a complete DOM inspection in one CDP round-trip and
// returns a structured PageAnalysis.
func (p *Inspector) Analyze() (*PageAnalysis, error) {
	raw, err := p.eval(analyzeScript)
	if err != nil {
		return nil, fmt.Errorf("pageintel: analyze: %w", err)
	}

	// The script returns a JSON string (double-encoded), unwrap it
	var jsonStr string
	if err := json.Unmarshal(raw, &jsonStr); err != nil {
		// Try direct object decode
		var analysis PageAnalysis
		if err2 := json.Unmarshal(raw, &analysis); err2 != nil {
			return nil, fmt.Errorf("pageintel: analyze decode: %w / %w", err, err2)
		}
		return &analysis, nil
	}

	var analysis PageAnalysis
	if err := json.Unmarshal([]byte(jsonStr), &analysis); err != nil {
		return nil, fmt.Errorf("pageintel: analyze unmarshal: %w", err)
	}
	return &analysis, nil
}

// SemanticMap is a token-optimized, human-readable summary of the page
// designed specifically for ingestion by an LLM in the Cognitive Engine.
type SemanticMap struct {
	Title       string   `json:"title"`
	URL         string   `json:"url"`
	PageType    string   `json:"pageType"`
	Summary     string   `json:"overview"`
	Interact    []string `json:"interactable_elements"`
	IsAwaiting  bool     `json:"is_awaiting_operator"`
}

// ObserveAndMap performs an analysis and condenses it into a SemanticMap.
func (p *Inspector) ObserveAndMap() (*SemanticMap, error) {
	analysis, err := p.Analyze()
	if err != nil {
		return nil, err
	}

	sm := &SemanticMap{
		Title:    analysis.Title,
		URL:      analysis.URL,
		PageType: string(analysis.PageType),
		Summary:  "Page contains " + fmt.Sprint(len(analysis.Inputs)) + " inputs and " + fmt.Sprint(len(analysis.Buttons)) + " buttons.",
	}
	if analysis.HasError {
		sm.Summary += " ERROR VISIBLE: " + analysis.ErrorText
	}
	if analysis.HasCaptcha {
		sm.Summary += " WARNING: Captcha/Bot-wall detected."
	}

	for _, inp := range analysis.Inputs {
		desc := inp.Type
		if inp.Name != "" {
			desc += " name=" + inp.Name
		}
		if inp.Placeholder != "" {
			desc += " placeholder='" + inp.Placeholder + "'"
		}
		if inp.Label != "" {
			desc += " label='" + inp.Label + "'"
		}
		sm.Interact = append(sm.Interact, "[Input] "+desc)
	}

	for _, btn := range analysis.Buttons {
		sm.Interact = append(sm.Interact, "[Button] "+btn.Text)
	}

	return sm, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Login Flow
// ─────────────────────────────────────────────────────────────────────────────

// Credentials holds login credentials — never log or serialize Password/TOTP.
type Credentials struct {
	Username string `json:"username"`
	Password string `json:"-"` // never serialized — security requirement
	TOTP     string `json:"-"` // never serialized — security requirement
}

// LoginOutcome describes the result of a login attempt.
type LoginOutcome struct {
	Success      bool     `json:"success"`
	NextPageType PageType `json:"nextPageType"`
	ErrorMsg     string   `json:"errorMsg,omitempty"`
	RequiresMFA  bool     `json:"requiresMFA"`
	NeedsHuman   string   `json:"needsHuman,omitempty"` // "captcha" | "mfa" | "blocked"
}

// FindUsernameSelector heuristically picks the username/email input from a PageAnalysis.
func FindUsernameSelector(analysis *PageAnalysis) string {
	userHints := []string{"user", "email", "login", "account", "identifier", "name"}
	for _, inp := range analysis.Inputs {
		combined := strings.ToLower(inp.Name + " " + inp.ID + " " + inp.Placeholder + " " + inp.Label)
		if inp.Type == "email" {
			return inp.Selector
		}
		for _, hint := range userHints {
			if strings.Contains(combined, hint) {
				return inp.Selector
			}
		}
	}
	// Last resort: first text input
	for _, inp := range analysis.Inputs {
		if inp.Type == "text" || inp.Type == "email" {
			return inp.Selector
		}
	}
	return ""
}

// FindPasswordSelector heuristically picks the password input.
func FindPasswordSelector(analysis *PageAnalysis) string {
	for _, inp := range analysis.Inputs {
		if inp.Type == "password" {
			return inp.Selector
		}
	}
	return ""
}

// FindSubmitSelector heuristically picks the primary submit button.
func FindSubmitSelector(analysis *PageAnalysis) string {
	for _, btn := range analysis.Buttons {
		if btn.Primary {
			return btn.Selector
		}
	}
	if len(analysis.Buttons) > 0 {
		return analysis.Buttons[0].Selector
	}
	return ""
}

// nativeFillScript returns JS that fills an input using native value setters
// so React/Vue/Angular state machines pick up the change event.
func nativeFillScript(selector, value string) string {
	return fmt.Sprintf(`(function(){
  var el = document.querySelector(%q);
  if (!el) return 'not_found';
  el.focus();
  var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
                            || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  if (nativeInputValueSetter) nativeInputValueSetter.call(el, %q);
  else el.value = %q;
  el.dispatchEvent(new Event('input',  {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return 'ok';
})()`, selector, value, value)
}

// clickScript returns JS that clicks an element and handles shadow DOM.
func clickScript(selector string) string {
	return fmt.Sprintf(`(function(){
  var el = document.querySelector(%q);
  if (!el) return 'not_found';
  el.scrollIntoView({behavior:'instant', block:'center'});
  el.click();
  return 'ok';
})()`, selector)
}

// LoginFlow performs a full automated login sequence using pageintel heuristics.
// It does NOT require callers to know any DOM selectors — it figures them
// out from the live page structure.
//
// Flow:
//  1. Analyze page to confirm it's a login page
//  2. Find username + password fields and submit button
//  3. Fill credentials (via native setter — works with React/Vue)
//  4. Click submit
//  5. Wait for navigation (simple poll)
//  6. Re-analyze: classify result as success/error/mfa/captcha
func (p *Inspector) LoginFlow(creds Credentials, navFn func(url string) error) (*LoginOutcome, error) {
	// Step 1: Analyze current page
	analysis, err := p.Analyze()
	if err != nil {
		return nil, fmt.Errorf("pageintel: login analyze: %w", err)
	}

	if analysis.HasCaptcha {
		return &LoginOutcome{Success: false, NeedsHuman: "captcha"}, nil
	}

	// Step 2: Find selectors
	userSel   := FindUsernameSelector(analysis)
	passSel   := FindPasswordSelector(analysis)
	submitSel := FindSubmitSelector(analysis)

	if userSel == "" || passSel == "" {
		return nil, fmt.Errorf("pageintel: login: could not detect username/password fields on page (type=%s)", analysis.PageType)
	}

	// Step 3: Fill username
	if _, err := p.eval(nativeFillScript(userSel, creds.Username)); err != nil {
		return nil, fmt.Errorf("pageintel: login: fill username: %w", err)
	}

	// Step 4: Fill password
	if _, err := p.eval(nativeFillScript(passSel, creds.Password)); err != nil {
		return nil, fmt.Errorf("pageintel: login: fill password: %w", err)
	}

	// Step 5: Submit
	if submitSel != "" {
		if _, err := p.eval(clickScript(submitSel)); err != nil {
			return nil, fmt.Errorf("pageintel: login: click submit: %w", err)
		}
	} else {
		// Press Enter as fallback
		if _, err := p.eval(`document.activeElement && document.activeElement.form && document.activeElement.form.submit()`); err != nil {
			return nil, fmt.Errorf("pageintel: login: submit form: %w", err)
		}
	}

	// Step 6: Wait for navigation signal (poll readyState for up to 5s)
	for i := 0; i < 25; i++ {
		raw, _ := p.eval(`document.readyState`)
		var state string
		json.Unmarshal(raw, &state) //nolint:errcheck
		if state == "complete" || state == "" {
			break
		}
	}

	// Step 7: Re-analyze for outcome
	postAnalysis, err := p.Analyze()
	if err != nil {
		// Navigation may have moved us — treat as potential success
		return &LoginOutcome{Success: true, NextPageType: PageTypeUnknown}, nil
	}

	if postAnalysis.HasCaptcha {
		return &LoginOutcome{Success: false, NeedsHuman: "captcha", NextPageType: postAnalysis.PageType}, nil
	}
	if postAnalysis.HasMFA {
		return &LoginOutcome{Success: false, RequiresMFA: true, NextPageType: PageTypeMFA}, nil
	}
	if postAnalysis.HasError && postAnalysis.ErrorText != "" {
		return &LoginOutcome{
			Success:      false,
			NextPageType: postAnalysis.PageType,
			ErrorMsg:     postAnalysis.ErrorText,
		}, nil
	}
	// If we moved away from the login page — success
	if postAnalysis.PageType != PageTypeLogin && postAnalysis.PageType != PageTypeError {
		return &LoginOutcome{Success: true, NextPageType: postAnalysis.PageType}, nil
	}
	// Still on login page with no error — ambiguous, return error
	return &LoginOutcome{
		Success:      false,
		NextPageType: postAnalysis.PageType,
		ErrorMsg:     "login did not navigate away from login page",
	}, nil
}
