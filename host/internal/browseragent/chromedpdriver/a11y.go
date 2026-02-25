// a11y.go — AriaSnapshot, Click, Fill implementations for chromedpdriver.
//
// AriaSnapshot runs a JS-based ARIA tree extraction (chromedp has no native
// aria_snapshot, so we replicate Playwright's output format via JS).
// Click and Fill use chromedp selectors with full emitter instrumentation.
package chromedpdriver

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/chromedp/chromedp"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/ipc"
)

// buildAriaJS returns a self-contained JS IIFE that walks the DOM from
// the given CSS selector root and produces a Playwright-format ARIA snapshot.
// The selector is embedded via %q so it is always a valid JS string literal.
func buildAriaJS(rootSelector string) string {
	return fmt.Sprintf(`(() => {
	const ROLE_MAP = {
		A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox',
		H1:'heading',H2:'heading',H3:'heading',H4:'heading',H5:'heading',H6:'heading',
		NAV:'navigation',MAIN:'main',HEADER:'banner',FOOTER:'contentinfo',IMG:'img',
	};
	function getRole(el){
		const r=el.getAttribute('role'); if(r) return r;
		if(el.tagName==='INPUT'){
			const t=el.type||'text';
			if(t==='checkbox') return 'checkbox';
			if(t==='radio') return 'radio';
			if(t==='submit'||t==='button') return 'button';
			return 'textbox';
		}
		return ROLE_MAP[el.tagName]||el.tagName.toLowerCase();
	}
	function getName(el){
		return el.getAttribute('aria-label')||el.getAttribute('placeholder')||
		       el.getAttribute('alt')||el.getAttribute('title')||
		       (el.tagName==='A'?el.getAttribute('href'):'')||
		       (el.textContent||'').trim().slice(0,80);
	}
	const skip=new Set(['SCRIPT','STYLE','NOSCRIPT','SVG','PATH','DEFS']);
	function walk(el,d){
		if(el.nodeType!==1||skip.has(el.tagName)) return '';
		const ro=getRole(el),na=getName(el),ind='  '.repeat(d);
		let s=ind+'- '+ro;
		if(na) s+=' "'+na.replace(/"/g,'\\"')+'"';
		s+='\n';
		if(el.tagName==='A'&&el.href) s+=ind+'  - /url: '+el.href+'\n';
		for(const c of el.children) s+=walk(c,d+1);
		return s;
	}
	const root=document.querySelector(%s);
	return root?walk(root,0):'';
})()`, fmt.Sprintf("%q", rootSelector))
}

// AriaSnapshot captures an ARIA-style tree from a page using headless JS.
func (d *Driver) AriaSnapshot(config browser.A11ySnapshotConfig) (*browser.A11ySnapshotResult, error) {
	selector := config.Selector
	if selector == "" {
		selector = "body"
	}

	page, err := d.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "a11y",
		Message: "Capturing ARIA snapshot",
	})

	var snapshot string
	if err := chromedp.Run(page.Ctx, chromedp.Evaluate(buildAriaJS(selector), &snapshot)); err != nil {
		return nil, fmt.Errorf("aria snapshot JS eval: %w", err)
	}

	nodeCount := 0
	for _, ln := range strings.Split(snapshot, "\n") {
		t := strings.TrimLeft(ln, " \t")
		if strings.HasPrefix(t, "- ") && !strings.HasPrefix(t, "- /url:") {
			nodeCount++
		}
	}
	return &browser.A11ySnapshotResult{
		Snapshot:  snapshot,
		NodeCount: nodeCount,
	}, nil
}

// Click clicks an element identified by selector on a page.
func (d *Driver) Click(config browser.ClickConfig) (*browser.ActionResult, error) {
	page, err := d.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "click",
		Message: "Clicking element: " + config.Selector,
	})

	timeout := config.TimeoutMs
	if timeout == 0 {
		timeout = 5000
	}

	// Build click action based on kind
	var action chromedp.Action
	switch config.Kind {
	case "role":
		// Best-effort: map "button[name='X']" → aria selector
		action = chromedp.Click(buildAriaSelector(config.Selector), chromedp.ByQuery)
	case "coords":
		// Parse "x,y" coords
		var x, y float64
		if _, scanErr := fmt.Sscanf(config.Selector, "%f,%f", &x, &y); scanErr == nil {
			action = chromedp.MouseClickXY(x, y)
		} else {
			return &browser.ActionResult{Ok: false, Error: "cannot parse coords: " + config.Selector}, nil
		}
	default: // css
		action = chromedp.Click(config.Selector, chromedp.ByQuery)
	}

	if runErr := chromedp.Run(page.Ctx, action); runErr != nil {
		errStr := runErr.Error()
		suggestion := "check selector"
		if strings.Contains(errStr, "not visible") {
			suggestion = "scroll element into view"
		} else if strings.Contains(errStr, "timeout") {
			suggestion = "wait for element to appear"
		}
		return &browser.ActionResult{Ok: false, Error: errStr, Suggestion: suggestion}, nil
	}

	return &browser.ActionResult{Ok: true}, nil
}

// Fill sets the value of a form field identified by selector.
func (d *Driver) Fill(config browser.FillConfig) (*browser.ActionResult, error) {
	page, err := d.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	d.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "fill",
		Message: "Filling field: " + config.Selector,
	})

	// JS injection: set .value and fire input+change events
	fillJS := fmt.Sprintf(`(() => {
		const el = document.querySelector(%q);
		if (!el) return false;
		el.focus();
		el.value = %s;
		el.dispatchEvent(new Event('input',  { bubbles: true }));
		el.dispatchEvent(new Event('change', { bubbles: true }));
		return true;
	})()`, config.Selector, mustMarshal(config.Value))

	var ok bool
	if runErr := chromedp.Run(page.Ctx, chromedp.Evaluate(fillJS, &ok)); runErr != nil {
		return &browser.ActionResult{Ok: false, Error: runErr.Error()}, nil
	}
	if !ok {
		return &browser.ActionResult{
			Ok:         false,
			Error:      "element not found: " + config.Selector,
			Suggestion: "verify selector or wait for element",
		}, nil
	}

	return &browser.ActionResult{Ok: true}, nil
}

// buildAriaSelector converts a role selector string to a best-effort CSS approximation.
// Example: "button[name='Log in']" → [aria-label='Log in'], button
func buildAriaSelector(roleSelector string) string {
	// Simple heuristic: if name is in brackets, try aria-label
	if strings.Contains(roleSelector, "[name") {
		start := strings.Index(roleSelector, "[")
		tag := roleSelector[:start]
		inner := roleSelector[start+1 : len(roleSelector)-1]
		// name='val' or name="val"
		name := strings.TrimPrefix(inner, "name=")
		name = strings.Trim(name, `'"`)
		return fmt.Sprintf(`%s[aria-label=%q], %s`, tag, name, tag)
	}
	return roleSelector
}

func mustMarshal(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
