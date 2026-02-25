// Package scriptengine provides the NAVIG Adaptive Script Engine.
//
// The script engine executes multi-step browser tasks with automatic retry,
// strategy escalation, and selector evolution when steps fail.
//
// # Step Types
//
//   - login       → full automated login via pageintel.LoginFlow
//   - goto        → navigate to URL
//   - fill        → fill input field (smart fallback chain)
//   - click       → click element (smart fallback chain)
//   - wait_for    → wait for selector or text to appear
//   - extract     → pull structured data (table/list/links/meta/text)
//   - screenshot  → capture screenshot
//   - eval        → run raw JS
//   - detect_outcome → classify what happened after last action
//   - analyze     → run full page analysis and return result
//
// # Fallback Strategy
//
// When a selector fails, the engine tries in order:
//  1. Exact CSS selector (given)
//  2. aria-label / data-testid / name / placeholder variants (EvolveSelector)
//  3. XPath equivalent
//  4. Heuristic (first visible input of matching type)
//  5. Emits SelectorEvolved IPC event with the winning selector for the forge panel
package scriptengine

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"navig-core/host/internal/browseragent/pageintel"
)

// ─────────────────────────────────────────────────────────────────────────────
// EvalFn + NavFn — driver abstractions
// ─────────────────────────────────────────────────────────────────────────────

// EvalFn executes JavaScript and returns raw JSON result bytes.
type EvalFn = pageintel.EvalFn

// NavFn navigates the browser to a URL.
type NavFn func(url string) error

// ShotFn takes a screenshot and saves it to path.
type ShotFn func(path string) error

// EventFn emits a named event with JSON payload (wired to IPC emitter).
type EventFn func(event string, payload interface{})

// ─────────────────────────────────────────────────────────────────────────────
// Step definitions
// ─────────────────────────────────────────────────────────────────────────────

// Step is a single browser automation action.
// Exactly one field should be set.
type Step struct {
	Login         *LoginStep         `json:"login,omitempty"`
	Goto          *GotoStep          `json:"goto,omitempty"`
	Fill          *FillStep          `json:"fill,omitempty"`
	Click         *ClickStep         `json:"click,omitempty"`
	WaitFor       *WaitForStep       `json:"waitFor,omitempty"`
	Extract       *ExtractStep       `json:"extract,omitempty"`
	Screenshot    *ScreenshotStep    `json:"screenshot,omitempty"`
	Eval          *EvalStep          `json:"eval,omitempty"`
	DetectOutcome *DetectOutcomeStep `json:"detectOutcome,omitempty"`
	Analyze       *AnalyzeStep       `json:"analyze,omitempty"`
	CognitiveFill *CognitiveFillStep `json:"cognitive_fill,omitempty"`
}

type LoginStep struct {
	URL      string `json:"url"`
	Username string `json:"username"`
	// Password intentionally omitted from JSON tag — passed via Credentials
}

type GotoStep struct {
	URL string `json:"url"`
}

type FillStep struct {
	Selector string `json:"selector"`
	Value    string `json:"value"`
	Hint     string `json:"hint,omitempty"` // semantic hint for evolution: "email" | "username" | "search"
}

type ClickStep struct {
	Selector string `json:"selector"`
	Text     string `json:"text,omitempty"` // fallback: match by button text
}

type WaitForStep struct {
	Selector  string `json:"selector,omitempty"`
	Text      string `json:"text,omitempty"`
	TimeoutMs int    `json:"timeoutMs,omitempty"`
}

type ExtractStep struct {
	Format string `json:"format"` // table | list | links | meta | text | inputs
}

type ScreenshotStep struct {
	Path string `json:"path,omitempty"`
}

type EvalStep struct {
	JS string `json:"js"`
}

type DetectOutcomeStep struct{}

type AnalyzeStep struct{}

type CognitiveFillStep struct {
	Intent  string `json:"intent"`
	Payload string `json:"payload,omitempty"`
	Submit  bool   `json:"submit,omitempty"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Step Result
// ─────────────────────────────────────────────────────────────────────────────

// StepResult is the output of a single executed step.
type StepResult struct {
	Index    int             `json:"index"`
	Type     string          `json:"type"`
	Status   string          `json:"status"`   // "ok" | "error" | "skipped" | "evolved"
	Data     json.RawMessage `json:"data,omitempty"`
	Error    string          `json:"error,omitempty"`
	Evolved  bool            `json:"evolved,omitempty"`   // true = selector was auto-replaced
	Duration time.Duration   `json:"-"`
	DurationMs int64         `json:"durationMs"`
}

// RunResult is the output of Engine.Run().
type RunResult struct {
	Steps      []StepResult `json:"steps"`
	TotalSteps int          `json:"totalSteps"`
	Succeeded  int          `json:"succeeded"`
	Failed     int          `json:"failed"`
	Evolved    int          `json:"evolved"`
	DurationMs int64        `json:"durationMs"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Engine
// ─────────────────────────────────────────────────────────────────────────────

// Engine is the NAVIG Adaptive Script Engine. Wire it with the driver's
// EvalFn, NavFn, ShotFn, and an optional EventFn for IPC events.
type Engine struct {
	eval   EvalFn
	nav    NavFn
	shot   ShotFn
	emit   EventFn
	intel  *pageintel.Inspector
	shotN  int // screenshot counter
}

// New creates an Engine. All functions except emit are required.
func New(eval EvalFn, nav NavFn, shot ShotFn, emit EventFn) *Engine {
	if emit == nil {
		emit = func(string, interface{}) {}
	}
	return &Engine{
		eval:  eval,
		nav:   nav,
		shot:  shot,
		emit:  emit,
		intel: pageintel.New(eval),
	}
}

// Run executes a sequence of steps with retry and selector evolution.
func (e *Engine) Run(steps []Step, creds *pageintel.Credentials) (*RunResult, error) {
	result := &RunResult{TotalSteps: len(steps)}
	start := time.Now()

	for i, step := range steps {
		sr := e.runStep(i, step, creds)
		result.Steps = append(result.Steps, sr)
		if sr.Status == "ok" || sr.Status == "evolved" {
			result.Succeeded++
		} else if sr.Status == "error" {
			result.Failed++
		}
		if sr.Evolved {
			result.Evolved++
		}
	}

	result.DurationMs = time.Since(start).Milliseconds()
	return result, nil
}

func (e *Engine) runStep(index int, step Step, creds *pageintel.Credentials) StepResult {
	start := time.Now()
	sr := StepResult{Index: index}

	defer func() {
		sr.DurationMs = time.Since(start).Milliseconds()
	}()

	switch {

	case step.Login != nil:
		sr.Type = "login"
		s := step.Login
		if err := e.nav(s.URL); err != nil {
			return e.fail(sr, fmt.Errorf("login goto: %w", err))
		}
		var c pageintel.Credentials
		if creds != nil {
			c = *creds
		}
		if s.Username != "" {
			c.Username = s.Username
		}
		outcome, err := e.intel.LoginFlow(c, e.nav)
		if err != nil {
			return e.fail(sr, err)
		}
		data, _ := json.Marshal(outcome)
		sr.Data = data
		if outcome.Success {
			sr.Status = "ok"
			e.emit("LoginSuccess", outcome)
		} else {
			sr.Status = "error"
			sr.Error = outcome.ErrorMsg
			if outcome.NeedsHuman != "" {
				sr.Error = "needs_human:" + outcome.NeedsHuman
			}
			e.emit("LoginFailed", outcome)
		}

	case step.Goto != nil:
		sr.Type = "goto"
		if err := e.nav(step.Goto.URL); err != nil {
			return e.fail(sr, err)
		}
		sr.Status = "ok"

	case step.Fill != nil:
		sr.Type = "fill"
		s := step.Fill
		ok, evolved, err := e.smartFill(s.Selector, s.Value, s.Hint)
		if err != nil {
			return e.fail(sr, err)
		}
		sr.Status = "ok"
		if evolved != "" {
			sr.Evolved = true
			sr.Status = "evolved"
			e.emit("SelectorEvolved", map[string]string{
				"original": s.Selector,
				"evolved":  evolved,
				"hint":     s.Hint,
			})
		}
		_ = ok

	case step.CognitiveFill != nil:
		sr.Type = "cognitive_fill"
		s := step.CognitiveFill
		
		// 1. Analyze page to find the most appropriate text area or input
		analysis, err := e.intel.Analyze()
		if err != nil {
			return e.fail(sr, fmt.Errorf("cognitive analysis failed: %w", err))
		}
		
		var bestInputSelector string
		for _, inp := range analysis.Inputs {
			if inp.Type == "text" || inp.Type == "textarea" || inp.Name == "text" {
				bestInputSelector = inp.Selector
				break
			}
		}
		if bestInputSelector == "" {
			return e.fail(sr, fmt.Errorf("cognitive fill: could not find any text inputs for payload"))
		}
		
		ok, evolved, err := e.smartFill(bestInputSelector, s.Payload, "message")
		if err != nil {
			return e.fail(sr, err)
		}
		
		if s.Submit {
			// Find submit button
			var submitSelector string
			for _, btn := range analysis.Buttons {
				if btn.Type == "submit" || strings.Contains(strings.ToLower(btn.Text), "submit") || strings.Contains(strings.ToLower(btn.Text), "post") || btn.Primary {
					submitSelector = btn.Selector
					break
				}
			}
			if submitSelector == "" {
				submitSelector = `button` // fallback
			}
			if _, _, err := e.smartClick(submitSelector, ""); err != nil {
				return e.fail(sr, fmt.Errorf("cognitive fill submit failed: %w", err))
			}
		}

		sr.Status = "ok"
		if evolved != "" {
			sr.Evolved = true
			sr.Status = "evolved"
			e.emit("SelectorEvolved", map[string]string{"original": bestInputSelector, "evolved": evolved, "hint": "message"})
		}
		_ = ok

	case step.Click != nil:
		sr.Type = "click"
		s := step.Click
		ok, evolved, err := e.smartClick(s.Selector, s.Text)
		if err != nil {
			return e.fail(sr, err)
		}
		sr.Status = "ok"
		if evolved != "" {
			sr.Evolved = true
			sr.Status = "evolved"
			e.emit("SelectorEvolved", map[string]string{
				"original": s.Selector,
				"evolved":  evolved,
			})
		}
		_ = ok

	case step.WaitFor != nil:
		sr.Type = "wait_for"
		s := step.WaitFor
		timeout := time.Duration(s.TimeoutMs) * time.Millisecond
		if timeout == 0 {
			timeout = 10 * time.Second
		}
		if err := e.waitFor(s.Selector, s.Text, timeout); err != nil {
			return e.fail(sr, err)
		}
		sr.Status = "ok"

	case step.Extract != nil:
		sr.Type = "extract"
		result, err := e.intel.Extract(pageintel.ExtractFormat(step.Extract.Format))
		if err != nil {
			return e.fail(sr, err)
		}
		data, _ := json.Marshal(result)
		sr.Data = data
		sr.Status = "ok"
		e.emit("Extracted", result)

	case step.Screenshot != nil:
		sr.Type = "screenshot"
		e.shotN++
		path := step.Screenshot.Path
		if path == "" {
			path = fmt.Sprintf("screenshot_%d.png", e.shotN)
		}
		if err := e.shot(path); err != nil {
			return e.fail(sr, err)
		}
		sr.Status = "ok"
		data, _ := json.Marshal(map[string]string{"path": path})
		sr.Data = data

	case step.Eval != nil:
		sr.Type = "eval"
		result, err := e.eval(step.Eval.JS)
		if err != nil {
			return e.fail(sr, err)
		}
		sr.Status = "ok"
		sr.Data = result

	case step.DetectOutcome != nil:
		sr.Type = "detect_outcome"
		outcome, err := e.intel.DetectOutcome()
		if err != nil {
			return e.fail(sr, err)
		}
		data, _ := json.Marshal(outcome)
		sr.Data = data
		sr.Status = "ok"
		e.emit("OutcomeDetected", outcome)

	case step.Analyze != nil:
		sr.Type = "analyze"
		analysis, err := e.intel.Analyze()
		if err != nil {
			return e.fail(sr, err)
		}
		data, _ := json.Marshal(analysis)
		sr.Data = data
		sr.Status = "ok"

	default:
		sr.Type = "unknown"
		sr.Status = "skipped"
	}

	return sr
}

// fail marks a StepResult as failed.
func (e *Engine) fail(sr StepResult, err error) StepResult {
	sr.Status = "error"
	sr.Error = err.Error()
	e.emit("StepFailed", map[string]interface{}{"index": sr.Index, "type": sr.Type, "error": sr.Error})
	return sr
}

// ─────────────────────────────────────────────────────────────────────────────
// Smart fill / click with selector evolution
// ─────────────────────────────────────────────────────────────────────────────

func (e *Engine) tryEval(js string) bool {
	result, err := e.eval(js)
	if err != nil {
		return false
	}
	var s string
	json.Unmarshal(result, &s) //nolint:errcheck
	return s == "ok"
}

// smartFill tries to fill an input, falling back through evolved selectors.
// Returns (ok, evolvedSelector, error).
func (e *Engine) smartFill(selector, value, hint string) (bool, string, error) {
	// 1. Try primary selector
	js := nativeFillScript(selector, value)
	if e.tryEval(js) {
		return true, "", nil
	}

	// 2. Evolve selector using the hint
	if hint == "" {
		hint = selectorHint(selector)
	}
	candidates, err := e.intel.EvolveSelector(hint)
	if err == nil {
		for _, c := range candidates {
			if e.tryEval(nativeFillScript(c.Selector, value)) {
				return true, c.Selector, nil
			}
		}
	}

	// 3. Last resort heuristic: first unfilled input of guessed type
	heuristicType := "text"
	if strings.Contains(strings.ToLower(hint), "pass") {
		heuristicType = "password"
	} else if strings.Contains(strings.ToLower(hint), "email") || strings.Contains(strings.ToLower(hint), "user") {
		heuristicType = "email"
	}
	heuristicJS := fmt.Sprintf(`(function(){
    var els = document.querySelectorAll('input[type=%q],input[type=text]');
    for (var i = 0; i < els.length; i++) {
      if (!els[i].value) {
        var niv = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
        if (niv) niv.call(els[i], %q); else els[i].value = %q;
        els[i].dispatchEvent(new Event('input',{bubbles:true}));
        return 'ok';
      }
    }
    return 'not_found';
  })()`, heuristicType, value, value)
	if e.tryEval(heuristicJS) {
		return true, "heuristic:" + heuristicType, nil
	}

	return false, "", fmt.Errorf("smartFill: could not fill %q — all strategies failed", selector)
}

// smartClick tries to click, falling back through evolved selectors or button text.
func (e *Engine) smartClick(selector, text string) (bool, string, error) {
	if selector != "" {
		if e.tryEval(clickScript(selector)) {
			return true, "", nil
		}
	}

	// Try by visible text
	if text != "" {
		js := fmt.Sprintf(`(function(){
      var all = document.querySelectorAll('button,[role=button],a,input[type=submit]');
      for (var i = 0; i < all.length; i++) {
        var t = (all[i].innerText || all[i].value || '').trim();
        if (t.toLowerCase().includes(%q)) {
          all[i].scrollIntoView({behavior:'instant',block:'center'});
          all[i].click();
          return 'ok';
        }
      }
      return 'not_found';
    })()`, strings.ToLower(text))
		if e.tryEval(js) {
			return true, "text:" + text, nil
		}
	}

	// Evolve selector
	hint := text
	if hint == "" {
		hint = selectorHint(selector)
	}
	candidates, err := e.intel.EvolveSelector(hint)
	if err == nil {
		for _, c := range candidates {
			if e.tryEval(clickScript(c.Selector)) {
				return true, c.Selector, nil
			}
		}
	}

	return false, "", fmt.Errorf("smartClick: could not click %q (text=%q) — all strategies failed", selector, text)
}

// waitFor polls for a selector or text to appear, up to timeout.
func (e *Engine) waitFor(selector, text string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if selector != "" {
			raw, _ := e.eval(fmt.Sprintf(`!!document.querySelector(%q)`, selector))
			var found bool
			json.Unmarshal(raw, &found) //nolint:errcheck
			if found {
				return nil
			}
		}
		if text != "" {
			raw, _ := e.eval(fmt.Sprintf(
				`(document.body ? document.body.innerText : '').includes(%q)`, text,
			))
			var found bool
			json.Unmarshal(raw, &found) //nolint:errcheck
			if found {
				return nil
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	return fmt.Errorf("waitFor: timeout after %s (selector=%q text=%q)", timeout, selector, text)
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared JS helpers (duplicated from pageintel for zero import cycle)
// ─────────────────────────────────────────────────────────────────────────────

func nativeFillScript(selector, value string) string {
	return fmt.Sprintf(`(function(){
  var el = document.querySelector(%q);
  if (!el) return 'not_found';
  el.focus();
  var niv = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
         || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set;
  if (niv) niv.call(el, %q); else el.value = %q;
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  return 'ok';
})()`, selector, value, value)
}

func clickScript(selector string) string {
	return fmt.Sprintf(`(function(){
  var el = document.querySelector(%q);
  if (!el) return 'not_found';
  el.scrollIntoView({behavior:'instant',block:'center'});
  el.click();
  return 'ok';
})()`, selector)
}

// selectorHint extracts a semantic hint from a CSS selector.
// e.g. "#email-input" → "email", "[name=password]" → "password"
func selectorHint(selector string) string {
	s := strings.ToLower(selector)
	for _, word := range []string{"email", "user", "pass", "login", "name", "search", "submit", "next", "continue"} {
		if strings.Contains(s, word) {
			return word
		}
	}
	return selector
}
