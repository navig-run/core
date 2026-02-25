package pageintel

import (
	"encoding/json"
	"fmt"
	"strings"
)

// ─────────────────────────────────────────────────────────────────────────────
// Data Extractor
// ─────────────────────────────────────────────────────────────────────────────

// ExtractFormat specifies what kind of data to pull from the page.
type ExtractFormat string

const (
	ExtractTable  ExtractFormat = "table"
	ExtractList   ExtractFormat = "list"
	ExtractLinks  ExtractFormat = "links"
	ExtractMeta   ExtractFormat = "meta"    // meta tags + JSON-LD + OpenGraph
	ExtractText   ExtractFormat = "text"    // clean body text
	ExtractInputs ExtractFormat = "inputs"  // current values of form inputs
)

// TableRow is one row of a table.
type TableRow []string

// LinkItem is a hyperlink.
type LinkItem struct {
	Text string `json:"text"`
	Href string `json:"href"`
}

// MetaData contains page metadata.
type MetaData struct {
	Title       string                 `json:"title"`
	Description string                 `json:"description"`
	OpenGraph   map[string]string      `json:"og,omitempty"`
	JsonLD      []map[string]interface{} `json:"jsonLd,omitempty"`
	Canonical   string                 `json:"canonical,omitempty"`
}

// ExtractionResult holds extracted data in structured form.
type ExtractionResult struct {
	Format  ExtractFormat          `json:"format"`
	Tables  [][]TableRow           `json:"tables,omitempty"`
	Lists   [][]string             `json:"lists,omitempty"`
	Links   []LinkItem             `json:"links,omitempty"`
	Meta    *MetaData              `json:"meta,omitempty"`
	Text    string                 `json:"text,omitempty"`
	Inputs  map[string]string      `json:"inputs,omitempty"`
	Raw     string                 `json:"raw,omitempty"`
}

const tableExtractScript = `(function(){
  var tables = Array.from(document.querySelectorAll('table')).map(tbl => {
    var rows = Array.from(tbl.querySelectorAll('tr')).map(tr =>
      Array.from(tr.querySelectorAll('th,td')).map(cell => cell.innerText.trim())
    );
    return rows;
  });
  return JSON.stringify(tables);
})()`

const listExtractScript = `(function(){
  var lists = Array.from(document.querySelectorAll('ul,ol')).map(ul =>
    Array.from(ul.querySelectorAll('li')).map(li => li.innerText.trim()).filter(t => t.length > 0)
  ).filter(l => l.length > 0);
  return JSON.stringify(lists);
})()`

const linksExtractScript = `(function(){
  var links = Array.from(document.querySelectorAll('a[href]'))
    .map(a => ({text: a.innerText.trim(), href: a.href}))
    .filter(l => l.text && l.href && !l.href.startsWith('javascript'));
  return JSON.stringify(links.slice(0,200));
})()`

const metaExtractScript = `(function(){
  var og = {};
  document.querySelectorAll('meta[property^="og:"]').forEach(m => {
    og[m.getAttribute('property').replace('og:','')] = m.getAttribute('content') || '';
  });
  var jsonLd = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try { jsonLd.push(JSON.parse(s.textContent)); } catch(_){}
  });
  var canonical = '';
  var canonEl = document.querySelector('link[rel="canonical"]');
  if (canonEl) canonical = canonEl.href;
  return JSON.stringify({
    title:       document.title,
    description: (document.querySelector('meta[name=description]')||{content:''}).content,
    og:          og,
    jsonLd:      jsonLd,
    canonical:   canonical,
  });
})()`

const textExtractScript = `(function(){
  // Remove scripts, styles, nav, header, footer for clean text
  var clone = document.body.cloneNode(true);
  ['script','style','nav','header','footer','[role=navigation]'].forEach(sel => {
    clone.querySelectorAll(sel).forEach(el => el.remove());
  });
  return JSON.stringify(clone.innerText.replace(/\n{3,}/g,'\n\n').trim().substring(0,8000));
})()`

const inputsExtractScript = `(function(){
  var inputs = {};
  document.querySelectorAll('input,textarea,select').forEach(el => {
    var key = el.name || el.id || el.placeholder || el.type;
    if (key && el.type !== 'password' && el.type !== 'hidden') {
      inputs[key] = el.value || '';
    }
  });
  return JSON.stringify(inputs);
})()`

// Extract pulls structured data from the live page.
// It never extracts password field values.
func (p *Inspector) Extract(format ExtractFormat) (*ExtractionResult, error) {
	result := &ExtractionResult{Format: format}

	switch format {
	case ExtractTable:
		raw, err := p.eval(tableExtractScript)
		if err != nil {
			return nil, fmt.Errorf("pageintel: extract table: %w", err)
		}
		var jsonStr string
		if err := json.Unmarshal(raw, &jsonStr); err == nil {
			raw = []byte(jsonStr)
		}
		var tables [][][]string
		if err := json.Unmarshal(raw, &tables); err != nil {
			return nil, fmt.Errorf("pageintel: extract table decode: %w", err)
		}
		for _, tbl := range tables {
			var rows []TableRow
			for _, row := range tbl {
				rows = append(rows, TableRow(row))
			}
			result.Tables = append(result.Tables, rows)
		}

	case ExtractList:
		raw, err := p.eval(listExtractScript)
		if err != nil {
			return nil, fmt.Errorf("pageintel: extract list: %w", err)
		}
		var jsonStr string
		if err := json.Unmarshal(raw, &jsonStr); err == nil {
			raw = []byte(jsonStr)
		}
		json.Unmarshal(raw, &result.Lists) //nolint:errcheck

	case ExtractLinks:
		raw, err := p.eval(linksExtractScript)
		if err != nil {
			return nil, fmt.Errorf("pageintel: extract links: %w", err)
		}
		var jsonStr string
		if err := json.Unmarshal(raw, &jsonStr); err == nil {
			raw = []byte(jsonStr)
		}
		json.Unmarshal(raw, &result.Links) //nolint:errcheck

	case ExtractMeta:
		raw, err := p.eval(metaExtractScript)
		if err != nil {
			return nil, fmt.Errorf("pageintel: extract meta: %w", err)
		}
		var jsonStr string
		if err := json.Unmarshal(raw, &jsonStr); err == nil {
			raw = []byte(jsonStr)
		}
		var meta MetaData
		if err := json.Unmarshal(raw, &meta); err != nil {
			return nil, fmt.Errorf("pageintel: extract meta decode: %w", err)
		}
		result.Meta = &meta

	case ExtractText:
		raw, err := p.eval(textExtractScript)
		if err != nil {
			return nil, fmt.Errorf("pageintel: extract text: %w", err)
		}
		json.Unmarshal(raw, &result.Text) //nolint:errcheck

	case ExtractInputs:
		raw, err := p.eval(inputsExtractScript)
		if err != nil {
			return nil, fmt.Errorf("pageintel: extract inputs: %w", err)
		}
		var jsonStr string
		if err := json.Unmarshal(raw, &jsonStr); err == nil {
			raw = []byte(jsonStr)
		}
		json.Unmarshal(raw, &result.Inputs) //nolint:errcheck

	default:
		return nil, fmt.Errorf("pageintel: unknown extract format %q (valid: table, list, links, meta, text, inputs)", format)
	}

	return result, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Outcome Detection
// ─────────────────────────────────────────────────────────────────────────────

// OutcomeStatus describes the result of a page action.
type OutcomeStatus string

const (
	OutcomeSuccess   OutcomeStatus = "success"
	OutcomeError     OutcomeStatus = "error"
	OutcomeRedirect  OutcomeStatus = "redirect"
	OutcomeCaptcha   OutcomeStatus = "captcha"
	OutcomeMFA       OutcomeStatus = "mfa"
	OutcomeBlocked   OutcomeStatus = "blocked"
	OutcomeUnchanged OutcomeStatus = "unchanged"
)

// Outcome describes what happened after a page action.
type Outcome struct {
	Status  OutcomeStatus `json:"status"`
	Detail  string        `json:"detail,omitempty"`
	URL     string        `json:"url"`
	Title   string        `json:"title"`
}

const outcomeScript = `(function(){
  var url   = window.location.href;
  var body  = document.body ? document.body.innerText : '';
  var title = document.title;
  var lower = (title + ' ' + body).toLowerCase();

  // ── 1. Bot Wall Detection (highest priority) ─────────────────────────────
  // Cloudflare Turnstile
  if (document.querySelector('#cf-wrapper, .cf-turnstile, #challenge-running, #cf-challenge-running, [data-cf-turnstile]')) {
    return JSON.stringify({status:'captcha', detail:'Cloudflare Turnstile detected', url:url, title:title});
  }
  // Cloudflare page text
  if (/cloudflare|just a moment|checking your browser|ddos-guard/.test(lower)) {
    return JSON.stringify({status:'captcha', detail:'Cloudflare bot wall detected', url:url, title:title});
  }
  // reCAPTCHA iframe
  var frames = document.querySelectorAll('iframe');
  for (var i = 0; i < frames.length; i++) {
    var src = frames[i].src || '';
    if (src.indexOf('recaptcha') !== -1 || src.indexOf('hcaptcha') !== -1 || src.indexOf('turnstile') !== -1) {
      return JSON.stringify({status:'captcha', detail:'CAPTCHA iframe detected: ' + src.substring(0,60), url:url, title:title});
    }
  }
  // hCaptcha / reCAPTCHA containers
  if (document.querySelector('.h-captcha, .g-recaptcha, [data-hcaptcha-widget-id], [data-sitekey]')) {
    return JSON.stringify({status:'captcha', detail:'CAPTCHA widget detected', url:url, title:title});
  }

  // ── 2. MFA / 2FA Detection ───────────────────────────────────────────────
  var mfaInputs = document.querySelectorAll('input[maxlength="6"], input[maxlength="8"], input[pattern*="[0-9]"]');
  var mfaText = /two.factor|2fa|verification code|enter.*code|one.time.password|otp|authenticator|sent.*phone|sent.*email.*code/;
  if (mfaInputs.length > 0 && mfaText.test(lower)) {
    return JSON.stringify({status:'mfa', detail:'MFA/2FA step detected', url:url, title:title});
  }
  if (mfaText.test(lower)) {
    return JSON.stringify({status:'mfa', detail:'MFA text detected', url:url, title:title});
  }

  // ── 3. Access Blocked ────────────────────────────────────────────────────
  if (/403 forbidden|access denied|your account.*blocked|suspended|banned|rate limited/.test(lower)) {
    return JSON.stringify({status:'blocked', detail:'Access denied or account blocked', url:url, title:title});
  }

  // ── 4. Error Detection ───────────────────────────────────────────────────
  // Collect visible error elements
  var errText = '';
  // aria-invalid fields
  var invalidFields = document.querySelectorAll('[aria-invalid=true], [aria-invalid="true"]');
  if (invalidFields.length > 0) errText = 'Field validation error (aria-invalid)';

  // Standard error containers
  var errSelectors = [
    '.error', '.alert-danger', '.alert-error', '[role="alert"]',
    '.error-message', '.form-error', '.notification--error',
    '.invalid-feedback', '[class*="error"][class*="text"]',
    '[class*="danger"]', '.text-red-500', '.text-red-600',
    'p.error', 'span.error', 'div.error',
  ];
  for (var j = 0; j < errSelectors.length; j++) {
    var el = document.querySelector(errSelectors[j]);
    if (el) {
      var t = el.innerText ? el.innerText.trim() : '';
      if (t.length > 2) { errText = t.substring(0, 200); break; }
    }
  }
  // Keyword-based error (fallback)
  if (!errText) {
    var errKeywords = /invalid.*password|invalid.*email|incorrect.*password|wrong password|login.*failed|authentication failed|try again|credentials.*incorrect/;
    if (errKeywords.test(lower)) errText = 'Login error detected by keyword';
  }
  if (errText) {
    return JSON.stringify({status:'error', detail:errText, url:url, title:title});
  }

  // ── 5. Success Detection ─────────────────────────────────────────────────
  // Logout link (strong indicator of authenticated state)
  var logoutEl = document.querySelector(
    '[href*="logout"], [href*="signout"], [href*="sign-out"], [href*="log-out"], ' +
    '[data-action*="logout"], [data-action*="signout"], ' +
    'a[href*="signout"], button[onclick*="logout"]'
  );
  if (logoutEl) {
    return JSON.stringify({status:'success', detail:'Logout link found (authenticated)', url:url, title:title});
  }

  // Profile / avatar icon
  var avatarEl = document.querySelector(
    '[class*="avatar"], [class*="profile-pic"], [class*="user-photo"], ' +
    'img[alt*="profile"], img[alt*="user"], img[alt*="avatar"], ' +
    '[data-component*="avatar"], [class*="UserAvatar"]'
  );
  if (avatarEl) {
    return JSON.stringify({status:'success', detail:'Profile/avatar element found (authenticated)', url:url, title:title});
  }

  // localStorage / sessionStorage tokens
  var tokenKeys = ['auth_token','access_token','token','session','jwt','id_token','bearer'];
  for (var k = 0; k < tokenKeys.length; k++) {
    if (localStorage.getItem(tokenKeys[k]) || sessionStorage.getItem(tokenKeys[k])) {
      return JSON.stringify({status:'success', detail:'Auth token in storage: ' + tokenKeys[k], url:url, title:title});
    }
  }

  // URL changed from /login to a non-login path
  if (url.indexOf('/login') === -1 && url.indexOf('/signin') === -1 && url.indexOf('/auth') === -1) {
    if (/dashboard|home|account|profile|overview|feed|inbox/.test(url.toLowerCase())) {
      return JSON.stringify({status:'redirect', detail:'Redirected to authenticated area', url:url, title:title});
    }
  }

  // Positive keyword signals
  if (/welcome back|you are logged in|successfully logged|signed in successfully/.test(lower)) {
    return JSON.stringify({status:'success', detail:'Success text detected', url:url, title:title});
  }

  return JSON.stringify({status:'unchanged', detail:'', url:url, title:title});
})()`

// DetectOutcome inspects the current page state and determines what happened
// after the last action (form submit, click, etc.).
func (p *Inspector) DetectOutcome() (*Outcome, error) {
	raw, err := p.eval(outcomeScript)
	if err != nil {
		return nil, fmt.Errorf("pageintel: detect outcome: %w", err)
	}

	var jsonStr string
	if err := json.Unmarshal(raw, &jsonStr); err == nil {
		raw = []byte(jsonStr)
	}

	var outcome Outcome
	if err := json.Unmarshal(raw, &outcome); err != nil {
		return nil, fmt.Errorf("pageintel: outcome decode: %w", err)
	}
	return &outcome, nil
}

// ─────────────────────────────────────────────────────────────────────────────
// Selector Evolution — find alternatives when a selector fails
// ─────────────────────────────────────────────────────────────────────────────

// SelectorCandidate is an alternative selector generated by evolution.
type SelectorCandidate struct {
	Selector  string  `json:"selector"`
	Strategy  string  `json:"strategy"`  // "aria", "name", "placeholder", "xpath", "heuristic"
	Confidence float64 `json:"confidence"` // 0–1
}

// EvolveSelector generates alternative selectors for a given input description
// when the primary selector has failed. This is the "auto-write" part of NavBrowser.
func (p *Inspector) EvolveSelector(hint string) ([]SelectorCandidate, error) {
	js := fmt.Sprintf(`(function(){
  var hint = %q.toLowerCase();
  var candidates = [];

  // Strategy 1: aria-label
  document.querySelectorAll('[aria-label]').forEach(el => {
    if (el.getAttribute('aria-label').toLowerCase().includes(hint)) {
      candidates.push({
        selector:   '[aria-label=' + JSON.stringify(el.getAttribute('aria-label')) + ']',
        strategy:   'aria',
        confidence: 0.9,
      });
    }
  });

  // Strategy 2: name attribute
  document.querySelectorAll('[name]').forEach(el => {
    if (el.getAttribute('name').toLowerCase().includes(hint)) {
      candidates.push({
        selector:   '[name=' + JSON.stringify(el.getAttribute('name')) + ']',
        strategy:   'name',
        confidence: 0.8,
      });
    }
  });

  // Strategy 3: placeholder
  document.querySelectorAll('[placeholder]').forEach(el => {
    if (el.getAttribute('placeholder').toLowerCase().includes(hint)) {
      candidates.push({
        selector:   '[placeholder=' + JSON.stringify(el.getAttribute('placeholder')) + ']',
        strategy:   'placeholder',
        confidence: 0.7,
      });
    }
  });

  // Strategy 4: data-testid
  document.querySelectorAll('[data-testid]').forEach(el => {
    if (el.getAttribute('data-testid').toLowerCase().includes(hint)) {
      candidates.push({
        selector:   '[data-testid=' + JSON.stringify(el.getAttribute('data-testid')) + ']',
        strategy:   'testid',
        confidence: 0.85,
      });
    }
  });

  // Strategy 5: label text
  document.querySelectorAll('label').forEach(lbl => {
    if (lbl.innerText.toLowerCase().includes(hint) && lbl.htmlFor) {
      candidates.push({
        selector:   '#' + lbl.htmlFor,
        strategy:   'label',
        confidence: 0.75,
      });
    }
  });

  return JSON.stringify(candidates.slice(0, 10));
})()`, strings.ToLower(hint))

	raw, err := p.eval(js)
	if err != nil {
		return nil, fmt.Errorf("pageintel: evolve selector: %w", err)
	}

	var jsonStr string
	if err := json.Unmarshal(raw, &jsonStr); err == nil {
		raw = []byte(jsonStr)
	}

	var candidates []SelectorCandidate
	if err := json.Unmarshal(raw, &candidates); err != nil {
		return nil, fmt.Errorf("pageintel: evolve decode: %w", err)
	}
	return candidates, nil
}
