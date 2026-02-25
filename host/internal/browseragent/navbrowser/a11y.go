// a11y.go — AriaSnapshot, Click, Fill additions for NavBrowser.
//
// NavBrowser delegates these operations to the chromedp JS engine directly,
// replicating Playwright's ARIA snapshot format via JS evaluation.
package navbrowser

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/chromedp/chromedp"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/ipc"
)

// AriaSnapshot extracts a Playwright-compatible ARIA tree from a page.
// The snapshot text uses the same "- role \"name\"" format as
// playwright's page.locator("body").ariaSnapshot().
func (nb *NavBrowser) AriaSnapshot(config browser.A11ySnapshotConfig) (*browser.A11ySnapshotResult, error) {
	page, err := nb.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	selector := config.Selector
	if selector == "" {
		selector = "body"
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	nb.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "a11y",
		Message: "Capturing ARIA snapshot via JS",
	})

	// Build the ARIA tree via DOM traversal in the page.
	js := buildAriaSnapshotJS(selector)
	var snapshot string
	if err := chromedp.Run(page.Ctx, chromedp.Evaluate(js, &snapshot)); err != nil {
		return nil, fmt.Errorf("navbrowser AriaSnapshot: %w", err)
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

// Click clicks an element on a page by CSS selector, role expression, or coordinates.
func (nb *NavBrowser) Click(config browser.ClickConfig) (*browser.ActionResult, error) {
	page, err := nb.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	nb.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "click",
		Message: "Clicking: " + config.Selector,
	})

	timeout := config.TimeoutMs
	if timeout == 0 {
		timeout = 5000
	}

	var action chromedp.Action
	switch config.Kind {
	case "coords":
		var x, y float64
		if _, scanErr := fmt.Sscanf(config.Selector, "%f,%f", &x, &y); scanErr == nil {
			action = chromedp.MouseClickXY(x, y)
		} else {
			return &browser.ActionResult{
				Ok:    false,
				Error: "cannot parse coords: " + config.Selector,
			}, nil
		}
	default: // css or role (best-effort CSS for role)
		sel := config.Selector
		if config.Kind == "role" {
			sel = roleToCSS(sel)
		}
		action = chromedp.Click(sel, chromedp.ByQuery)
	}

	if runErr := chromedp.Run(page.Ctx, action); runErr != nil {
		errStr := runErr.Error()
		sug := "check selector"
		if strings.Contains(errStr, "not visible") {
			sug = "scroll element into view"
		} else if strings.Contains(errStr, "deadline") || strings.Contains(errStr, "timeout") {
			sug = "wait for element to appear"
		}
		return &browser.ActionResult{Ok: false, Error: errStr, Suggestion: sug}, nil
	}

	return &browser.ActionResult{Ok: true}, nil
}

// Fill sets a form field's value using JS injection (.value + fire events).
func (nb *NavBrowser) Fill(config browser.FillConfig) (*browser.ActionResult, error) {
	page, err := nb.findPage(config.PageId)
	if err != nil {
		return nil, err
	}

	evCtx := ipc.EventCtx{SessionID: page.SessionID, PageID: page.ID}
	nb.emitter.Status(evCtx, "info", ipc.StatusData{
		Phase:   "fill",
		Message: "Filling: " + config.Selector,
	})

	valJSON, _ := json.Marshal(config.Value)
	js := fmt.Sprintf(`(() => {
		const el = document.querySelector(%q);
		if (!el) return false;
		el.focus();
		el.value = %s;
		el.dispatchEvent(new Event('input',  { bubbles: true }));
		el.dispatchEvent(new Event('change', { bubbles: true }));
		return true;
	})()`, config.Selector, string(valJSON))

	var ok bool
	if runErr := chromedp.Run(page.Ctx, chromedp.Evaluate(js, &ok)); runErr != nil {
		return &browser.ActionResult{Ok: false, Error: runErr.Error()}, nil
	}
	if !ok {
		return &browser.ActionResult{
			Ok:         false,
			Error:      "element not found: " + config.Selector,
			Suggestion: "verify selector or wait for element visibility",
		}, nil
	}
	return &browser.ActionResult{Ok: true}, nil
}

// ── helpers ───────────────────────────────────────────────────────────────────

// buildAriaSnapshotJS returns a self-contained JS expression that walks
// the DOM from the given selector and produces a Playwright-format ARIA tree.
func buildAriaSnapshotJS(rootSelector string) string {
	return fmt.Sprintf(`(() => {
	const TAGS = {
		A:'link',BUTTON:'button',TEXTAREA:'textbox',SELECT:'combobox',
		H1:'heading',H2:'heading',H3:'heading',H4:'heading',H5:'heading',H6:'heading',
		NAV:'navigation',MAIN:'main',HEADER:'banner',FOOTER:'contentinfo',IMG:'img',
	};
	function role(el){
		const r=el.getAttribute('role'); if(r) return r;
		if(el.tagName==='INPUT'){
			const t=el.type||'text';
			if(t==='checkbox')return 'checkbox';
			if(t==='radio')return 'radio';
			if(t==='submit'||t==='button')return 'button';
			return 'textbox';
		}
		return TAGS[el.tagName]||el.tagName.toLowerCase();
	}
	function name(el){
		return el.getAttribute('aria-label')||el.getAttribute('placeholder')||
		       el.getAttribute('alt')||(el.tagName==='A'?el.getAttribute('href'):'')||
		       (el.textContent||'').trim().slice(0,80);
	}
	function walk(el,d){
		if(el.nodeType!==1)return '';
		const skip=new Set(['SCRIPT','STYLE','NOSCRIPT','SVG','PATH']);
		if(skip.has(el.tagName))return '';
		const ro=role(el),na=name(el),ind='  '.repeat(d);
		let s=ind+'- '+ro;
		if(na)s+=' "'+na.replace(/"/g,'\\"')+'"';
		s+='\n';
		if(el.tagName==='A'&&el.href)s+=ind+'  - /url: '+el.href+'\n';
		for(const c of el.children)s+=walk(c,d+1);
		return s;
	}
	const root=document.querySelector(%q);
	return root?walk(root,0):'';
})()`, rootSelector)
}

// roleToCSS converts a role selector like "button[name='Log in']" to a
// best-effort CSS approximation for chromedp's ByQuery.
func roleToCSS(sel string) string {
	if !strings.Contains(sel, "[name") {
		return sel
	}
	bracket := strings.Index(sel, "[")
	tag := sel[:bracket]
	inner := strings.Trim(sel[bracket+1:len(sel)-1], `'"`+" ")
	name := strings.TrimPrefix(inner, "name=")
	name = strings.Trim(name, `'"`)
	return fmt.Sprintf(`%s[aria-label=%q], %s`, tag, name, tag)
}
