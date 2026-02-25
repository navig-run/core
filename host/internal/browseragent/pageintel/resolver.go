package pageintel

import (
	"encoding/json"
	"fmt"
	"strings"
)

// ─────────────────────────────────────────────────────────────────────────────
// 3-Tier Self-Healing Target Resolver
//
// Tier 1 — Historical/Cached: Use the exact selector that worked last time.
//           Fast. Loaded from SelectorKnowledge store.
//
// Tier 2 — Semantic & Accessibility: Scan DOM for aria-label, placeholder,
//           name attributes, or <label for="..."> text. Framework-agnostic.
//
// Tier 3 — Visual / Heuristic Inference: Read page structure. Only one
//           <input type="password"> exists? Use it. Need email? Use the input
//           immediately before the password field. Need submit? Score all
//           buttons by intent text.
// ─────────────────────────────────────────────────────────────────────────────

// FieldSemantic is a human-readable field role used for Tier 2/3 resolution.
type FieldSemantic string

const (
	SemanticUsername FieldSemantic = "username" // email/username/login
	SemanticPassword FieldSemantic = "password"
	SemanticSubmit   FieldSemantic = "submit"
	SemanticSearch   FieldSemantic = "search"
	SemanticCheckout FieldSemantic = "checkout" // purchase/buy intent
	SemanticNext     FieldSemantic = "next"
	SemanticGeneric  FieldSemantic = "generic"
)

// TargetHint describes what we want to find, at multiple levels of specificity.
type TargetHint struct {
	// Semantic role — what this field IS, not where it is.
	Semantic FieldSemantic `json:"semantic"`

	// Tier1 is the last-known working CSS selector (from knowledge base).
	// Empty string = skip Tier 1.
	Tier1 string `json:"tier1,omitempty"`

	// FieldType narrows Tier 3 structural inference ("email"|"password"|"text"|"button").
	FieldType string `json:"fieldType,omitempty"`

	// Intent is a freeform purchase/action intent for A/B test aware button scoring.
	// e.g. "purchase", "login", "subscribe"
	Intent string `json:"intent,omitempty"`
}

// ResolvedTarget is the outcome of the 3-tier resolution process.
type ResolvedTarget struct {
	// Selector is the winning CSS selector.
	Selector string `json:"selector"`

	// Tier records which tier found this selector (1, 2, or 3).
	Tier int `json:"tier"`

	// Strategy describes the exact mechanism used within that tier.
	Strategy string `json:"strategy"`

	// Evolved is true when Tier1 failed — the caller should update the knowledge base.
	Evolved bool `json:"evolved"`
}

// ─────────────────────────────────────────────────────────────────────────────
// Tier 1 — try the cached selector
// ─────────────────────────────────────────────────────────────────────────────

// tier1Script tests whether a selector matches and returns "ok" or "not_found".
func tier1Script(selector string) string {
	return fmt.Sprintf(`(function(){
  var el = document.querySelector(%q);
  if (!el) return 'not_found';
  var style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return 'not_found';
  return 'ok';
})()`, selector)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tier 2 — semantic / accessibility DOM scan
// ─────────────────────────────────────────────────────────────────────────────

// tier2Script returns a JS bundle that scans the DOM using semantic signals.
// It returns the best CSS selector or empty string.
func tier2Script(semantic FieldSemantic) string {
	// Build per-semantic scan strategies
	var scanLogic string

	switch semantic {
	case SemanticUsername:
		scanLogic = `
  // aria-label containing email/user/login keywords
  var emailHints = ['email', 'user', 'login', 'account', 'identifier', 'username', 'e-mail'];
  var candidates = [];

  // Strategy A: type=email input (most reliable)
  document.querySelectorAll('input[type=email]').forEach(function(el) {
    candidates.push({sel: bestSelector(el), score: 10});
  });

  // Strategy B: aria-label / placeholder / name
  document.querySelectorAll('input').forEach(function(el) {
    var attrs = [el.getAttribute('aria-label')||'', el.placeholder||'', el.name||'', el.id||''];
    var text = attrs.join(' ').toLowerCase();
    for (var i = 0; i < emailHints.length; i++) {
      if (text.indexOf(emailHints[i]) !== -1) {
        candidates.push({sel: bestSelector(el), score: 8 - i});
        break;
      }
    }
  });

  // Strategy C: label text
  document.querySelectorAll('label').forEach(function(lbl) {
    var text = lbl.innerText.toLowerCase();
    for (var i = 0; i < emailHints.length; i++) {
      if (text.indexOf(emailHints[i]) !== -1 && lbl.htmlFor) {
        candidates.push({sel: '#' + lbl.htmlFor, score: 7 - i});
        break;
      }
    }
  });`

	case SemanticPassword:
		scanLogic = `
  var candidates = [];

  // Strategy A: type=password (extremely reliable)
  document.querySelectorAll('input[type=password]').forEach(function(el) {
    candidates.push({sel: bestSelector(el), score: 10});
  });

  // Strategy B: aria-label containing "pass"
  document.querySelectorAll('input').forEach(function(el) {
    var text = [(el.getAttribute('aria-label')||''), el.placeholder||'', el.name||''].join(' ').toLowerCase();
    if (text.indexOf('pass') !== -1 || text.indexOf('secret') !== -1) {
      candidates.push({sel: bestSelector(el), score: 8});
    }
  });`

	case SemanticSubmit:
		scanLogic = `
  var candidates = [];
  var submitHints = ['sign in', 'log in', 'login', 'submit', 'continue', 'next', 'proceed'];

  // Strategy A: type=submit
  document.querySelectorAll('input[type=submit], button[type=submit]').forEach(function(el) {
    candidates.push({sel: bestSelector(el), score: 10});
  });

  // Strategy B: button / role=button with submit-looking text
  document.querySelectorAll('button, [role=button], div[onclick], a.btn').forEach(function(el) {
    var text = (el.innerText || el.value || '').trim().toLowerCase();
    for (var i = 0; i < submitHints.length; i++) {
      if (text.indexOf(submitHints[i]) !== -1) {
        candidates.push({sel: bestSelector(el), score: 9 - i});
        break;
      }
    }
  });`

	case SemanticCheckout:
		scanLogic = `
  var candidates = [];
  // A/B test aware: score by purchase intent keywords
  var keywords = [
    {word: 'secure checkout', score: 10},
    {word: 'checkout',        score: 9},
    {word: 'buy now',         score: 9},
    {word: 'purchase',        score: 8},
    {word: 'add to cart',     score: 7},
    {word: 'order now',       score: 7},
    {word: 'complete order',  score: 8},
    {word: 'pay now',         score: 9},
    {word: 'place order',     score: 8},
  ];
  document.querySelectorAll('button, [role=button], a, input[type=submit], div[onclick]').forEach(function(el) {
    var text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
    for (var i = 0; i < keywords.length; i++) {
      if (text.indexOf(keywords[i].word) !== -1) {
        var rect = el.getBoundingClientRect();
        var visible = rect.width > 0 && rect.height > 0;
        if (visible) candidates.push({sel: bestSelector(el), score: keywords[i].score});
        break;
      }
    }
  });`

	default:
		scanLogic = `var candidates = [];`
	}

	return fmt.Sprintf(`(function(){
  function bestSelector(el) {
    if (el.id) return '#' + el.id;
    if (el.name) return '[name=' + JSON.stringify(el.name) + ']';
    var tid = el.getAttribute('data-testid');
    if (tid) return '[data-testid=' + JSON.stringify(tid) + ']';
    var aria = el.getAttribute('aria-label');
    if (aria) return '[aria-label=' + JSON.stringify(aria) + ']';
    // nth-child path
    var path = [], cur = el;
    while (cur && cur !== document.body) {
      var tag = cur.tagName ? cur.tagName.toLowerCase() : '';
      if (!tag) break;
      var sibs = cur.parentNode ? Array.from(cur.parentNode.children).filter(c => c.tagName === cur.tagName) : [];
      path.unshift(sibs.length > 1 ? tag + ':nth-of-type(' + (sibs.indexOf(cur)+1) + ')' : tag);
      cur = cur.parentNode;
    }
    return path.join(' > ');
  }

  %s

  // Sort by score descending
  candidates.sort(function(a, b) { return b.score - a.score; });

  // Filter: must be in DOM and visible
  for (var i = 0; i < candidates.length; i++) {
    var el = document.querySelector(candidates[i].sel);
    if (!el) continue;
    var style = window.getComputedStyle(el);
    if (style.display !== 'none' && style.visibility !== 'hidden') {
      return candidates[i].sel;
    }
  }
  return '';
})()`, scanLogic)
}

// ─────────────────────────────────────────────────────────────────────────────
// Tier 3 — structural / visual heuristic inference
// ─────────────────────────────────────────────────────────────────────────────

// tier3Script uses structural DOM reasoning — the last resort before giving up.
func tier3Script(semantic FieldSemantic) string {
	switch semantic {
	case SemanticPassword:
		// There's usually exactly one <input type="password"> on a login page.
		return `(function(){
  var inputs = Array.from(document.querySelectorAll('input[type=password]'));
  if (inputs.length === 1) {
    var el = inputs[0];
    if (el.id) return JSON.stringify('#' + el.id);
    if (el.name) return '[name=' + JSON.stringify(el.name) + ']';
    return 'input[type=password]';
  }
  return '';
})()`

	case SemanticUsername:
		// Find the input immediately before the password field.
		return `(function(){
  var pwField = document.querySelector('input[type=password]');
  if (!pwField) return '';

  // Walk backwards through all inputs to find the one before the password
  var allInputs = Array.from(document.querySelectorAll('input:not([type=password]):not([type=hidden]):not([type=submit])'));
  // Filter to visible ones
  allInputs = allInputs.filter(function(el) {
    var s = window.getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden';
  });

  // Find the input whose position in document order is just before the password field
  var pwPos = pwField.compareDocumentPosition ? 0 : -1;
  var best = null;
  for (var i = 0; i < allInputs.length; i++) {
    var rel = allInputs[i].compareDocumentPosition(pwField);
    if (rel & Node.DOCUMENT_POSITION_FOLLOWING) {
      best = allInputs[i]; // this input comes before the password field
    }
  }
  if (!best) best = allInputs[0];
  if (!best) return '';

  if (best.id) return '#' + best.id;
  if (best.name) return '[name=' + JSON.stringify(best.name) + ']';
  var type = best.type || 'text';
  return 'input[type=' + type + ']';
})()`

	case SemanticSubmit:
		// Find the most prominent clickable element near the password field.
		return `(function(){
  var pwField = document.querySelector('input[type=password]');
  var submitWords = /sign.?in|log.?in|login|submit|continue|next|proceed|enter/i;

  // First: any submit near the password field
  var form = pwField ? pwField.closest('form') : null;
  if (form) {
    var sub = form.querySelector('[type=submit], button');
    if (sub) {
      if (sub.id) return '#' + sub.id;
      return sub.tagName.toLowerCase() + (sub.type ? '[type=' + sub.type + ']' : '');
    }
  }

  // Fallback: best-scoring button on page
  var buttons = Array.from(document.querySelectorAll('button, [role=button], input[type=submit]'));
  buttons = buttons.filter(function(el) {
    var s = window.getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden';
  });
  for (var i = 0; i < buttons.length; i++) {
    var text = (buttons[i].innerText || buttons[i].value || '').trim();
    if (submitWords.test(text)) {
      var el = buttons[i];
      if (el.id) return '#' + el.id;
      return el.tagName.toLowerCase();
    }
  }
  // Last resort: last visible button in document
  if (buttons.length > 0) {
    var el = buttons[buttons.length - 1];
    if (el.id) return '#' + el.id;
    return el.tagName.toLowerCase();
  }
  return '';
})()`

	case SemanticCheckout:
		// Score all interactive elements by purchase intent and return the winner.
		return `(function(){
  var keywords = ['secure checkout','checkout','buy now','purchase','add to cart','order now','pay now','place order'];
  var scored = [];
  document.querySelectorAll('button,[role=button],a,input[type=submit]').forEach(function(el) {
    var text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
    for (var i = 0; i < keywords.length; i++) {
      if (text.indexOf(keywords[i]) !== -1) {
        var rect = el.getBoundingClientRect();
        if (rect.width > 0) scored.push({el: el, w: rect.width * rect.height, i: i});
        break;
      }
    }
  });
  if (!scored.length) return '';
  scored.sort(function(a,b) { return a.i - b.i || b.w - a.w; });
  var el = scored[0].el;
  if (el.id) return '#' + el.id;
  return el.tagName.toLowerCase();
})()`

	default:
		return `''`
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// ResolveTarget — the main 3-tier waterfall
// ─────────────────────────────────────────────────────────────────────────────

// ResolveTarget is the heart of the self-healing engine.
// It tries progressively smarter strategies until it finds a working selector.
//
// Returns the resolved selector and metadata about how it was found.
// Returns an error only if all 3 tiers fail completely.
func (p *Inspector) ResolveTarget(hint TargetHint) (*ResolvedTarget, error) {
	eval := p.eval

	// ── Tier 1: try cached selector ─────────────────────────────────────────
	if hint.Tier1 != "" {
		raw, err := eval(tier1Script(hint.Tier1))
		if err == nil {
			var status string
			if json.Unmarshal(raw, &status) == nil && status == "ok" {
				return &ResolvedTarget{
					Selector: hint.Tier1,
					Tier:     1,
					Strategy: "cached",
					Evolved:  false,
				}, nil
			}
		}
	}

	// ── Tier 2: semantic / accessibility scan ────────────────────────────────
	raw, err := eval(tier2Script(hint.Semantic))
	if err == nil {
		var sel string
	// The script returns a plain string (CDP JSON-encodes it automatically)
		if json.Unmarshal(raw, &sel) == nil && sel != "" {
			return &ResolvedTarget{
				Selector: sel,
				Tier:     2,
				Strategy: "semantic_accessibility",
				Evolved:  hint.Tier1 != "", // tier1 existed but failed → evolved
			}, nil
		}
	}

	// ── Tier 3: structural / visual heuristic ─────────────────────────────────
	raw, err = eval(tier3Script(hint.Semantic))
	if err == nil {
		var sel string
		if json.Unmarshal(raw, &sel) == nil && sel != "" {
			return &ResolvedTarget{
				Selector: sel,
				Tier:     3,
				Strategy: "structural_heuristic",
				Evolved:  true,
			}, nil
		}
	}

	return nil, fmt.Errorf("pageintel: ResolveTarget: all 3 tiers failed for semantic=%q (tried tier1=%q)", hint.Semantic, hint.Tier1)
}

// ─────────────────────────────────────────────────────────────────────────────
// Convenience: HealingFill — resolve then fill
// ─────────────────────────────────────────────────────────────────────────────

// HealingFillResult carries both the fill outcome and the resolution metadata.
type HealingFillResult struct {
	Resolved *ResolvedTarget
	FillOK   bool
}

// HealingFill resolves the field via 3-tier waterfall then fills it using
// the native setter (React/Vue/Angular compatible).
//
// The knowledge base is updated automatically if tier > 1.
func (p *Inspector) HealingFill(hint TargetHint, value string, kb *SelectorKnowledge, domain string) (*HealingFillResult, error) {
	resolved, err := p.ResolveTarget(hint)
	if err != nil {
		return nil, err
	}

	// Fill with native setter
	raw, fillErr := p.eval(nativeFillScript(resolved.Selector, value))
	if fillErr != nil {
		return nil, fmt.Errorf("pageintel: HealingFill: fill failed: %w", fillErr)
	}
	var fillStatus string
	json.Unmarshal(raw, &fillStatus) //nolint:errcheck
	fillOK := fillStatus == "ok"

	// Persist evolved selector to knowledge base
	if resolved.Evolved && kb != nil && domain != "" {
		kb.Save(domain, string(hint.Semantic), SelectorRecord{
			Selector: resolved.Selector,
			Tier:     resolved.Tier,
			Strategy: resolved.Strategy,
		})
	}

	return &HealingFillResult{Resolved: resolved, FillOK: fillOK}, nil
}

// HealingClick resolves a clickable target via 3-tier waterfall then clicks it.
func (p *Inspector) HealingClick(hint TargetHint, kb *SelectorKnowledge, domain string) (*ResolvedTarget, error) {
	resolved, err := p.ResolveTarget(hint)
	if err != nil {
		return nil, err
	}

	if _, err := p.eval(clickScript(resolved.Selector)); err != nil {
		return nil, fmt.Errorf("pageintel: HealingClick: %w", err)
	}

	// Persist
	if resolved.Evolved && kb != nil && domain != "" {
		kb.Save(domain, string(hint.Semantic), SelectorRecord{
			Selector: resolved.Selector,
			Tier:     resolved.Tier,
			Strategy: resolved.Strategy,
		})
	}

	return resolved, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Enhanced LoginFlow using 3-tier resolution
// ─────────────────────────────────────────────────────────────────────────────

// HealingLoginFlow is the 3-tier aware login flow.
// Unlike LoginFlow (which calls EvolveSelector), this uses ResolveTarget
// so the exact tier + strategy is tracked and persisted.
func (p *Inspector) HealingLoginFlow(creds Credentials, domain string, kb *SelectorKnowledge) (*LoginOutcome, error) {
	// Step 1: Check for captcha before even attempting
	analysis, err := p.Analyze()
	if err != nil {
		return nil, fmt.Errorf("pageintel: healing login: analyze: %w", err)
	}
	if analysis.HasCaptcha {
		return &LoginOutcome{Success: false, NeedsHuman: "captcha"}, nil
	}

	// Step 2: Resolve username (use knowledge base for Tier 1)
	var tier1User, tier1Pass, tier1Submit string
	if kb != nil {
		if rec, ok := kb.Get(domain, string(SemanticUsername)); ok {
			tier1User = rec.Selector
		}
		if rec, ok := kb.Get(domain, string(SemanticPassword)); ok {
			tier1Pass = rec.Selector
		}
		if rec, ok := kb.Get(domain, string(SemanticSubmit)); ok {
			tier1Submit = rec.Selector
		}
	}

	// Step 3: Fill username
	userResult, err := p.HealingFill(TargetHint{
		Semantic:  SemanticUsername,
		Tier1:     tier1User,
		FieldType: "email",
	}, creds.Username, kb, domain)
	if err != nil {
		return nil, fmt.Errorf("pageintel: healing login: username: %w", err)
	}

	// Step 4: Fill password
	passResult, err := p.HealingFill(TargetHint{
		Semantic:  SemanticPassword,
		Tier1:     tier1Pass,
		FieldType: "password",
	}, creds.Password, kb, domain)
	if err != nil {
		return nil, fmt.Errorf("pageintel: healing login: password: %w", err)
	}

	// Step 5: Click submit
	submitResult, err := p.HealingClick(TargetHint{
		Semantic:  SemanticSubmit,
		Tier1:     tier1Submit,
		Intent:    "login",
	}, kb, domain)
	if err != nil {
		return nil, fmt.Errorf("pageintel: healing login: submit: %w", err)
	}

	// Track evolution metadata (useful for the forge panel)
	_ = userResult
	_ = passResult
	_ = submitResult

	// Step 6: Wait for page state to settle
	for i := 0; i < 20; i++ {
		raw, _ := p.eval(`document.readyState`)
		var state string
		json.Unmarshal(raw, &state) //nolint:errcheck
		if state == "complete" {
			break
		}
	}

	// Step 7: Detect outcome
	outcome, err := p.DetectOutcome()
	if err != nil {
		return &LoginOutcome{Success: true, NextPageType: PageTypeUnknown}, nil
	}

	switch outcome.Status {
	case OutcomeCaptcha:
		return &LoginOutcome{Success: false, NeedsHuman: "captcha", NextPageType: PageTypeCaptcha}, nil
	case OutcomeMFA:
		return &LoginOutcome{Success: false, RequiresMFA: true, NextPageType: PageTypeMFA}, nil
	case OutcomeBlocked:
		return &LoginOutcome{Success: false, NeedsHuman: "blocked"}, nil
	case OutcomeError:
		return &LoginOutcome{Success: false, ErrorMsg: outcome.Detail, NextPageType: PageTypeError}, nil
	case OutcomeSuccess, OutcomeRedirect:
		return &LoginOutcome{Success: true, NextPageType: PageTypeDashboard}, nil
	default:
		// "unchanged" — check if we're no longer on a login page
		postAnalysis, _ := p.Analyze()
		if postAnalysis != nil && postAnalysis.PageType != PageTypeLogin && postAnalysis.PageType != PageTypeError {
			return &LoginOutcome{Success: true, NextPageType: postAnalysis.PageType}, nil
		}
		return &LoginOutcome{
			Success:  false,
			ErrorMsg: strings.TrimSpace("login page unchanged after submit: " + outcome.Detail),
		}, nil
	}
}
