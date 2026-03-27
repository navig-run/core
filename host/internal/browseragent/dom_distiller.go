// navig-core/host/internal/browseragent/dom_distiller.go
// Package browseragent provides the DOM-to-Markdown distiller for
// converting live browser pages into compact, LLM-optimized action trees.
//
// Usage:
//   js := DOMDistillerScript()
//   result, err := driver.Eval(EvalConfig{PageId: page.PageId, Js: js})
//   tree, err := ParseDistilledTree(result.Result)
package browseragent

import (
	"encoding/json"
	"fmt"
	"strings"
)

// DistilledElement is a single interactive element from the DOM distiller.
type DistilledElement struct {
	Index       int    `json:"index"`
	Tag         string `json:"tag"`
	Type        string `json:"type,omitempty"`    // for inputs
	Text        string `json:"text,omitempty"`    // button text, label, aria-label
	Placeholder string `json:"placeholder,omitempty"`
	Href        string `json:"href,omitempty"`
	Value       string `json:"value,omitempty"`
	ID          string `json:"id,omitempty"`
	Name        string `json:"name,omitempty"`
	Handle      string `json:"handle"`            // stable UUID assigned by JS
	Rect        struct {
		Left   int `json:"left"`
		Top    int `json:"top"`
		Right  int `json:"right"`
		Bottom int `json:"bottom"`
	} `json:"rect"`
}

// DistilledPage is the full distilled output for a browser page.
type DistilledPage struct {
	Title    string             `json:"title"`
	URL      string             `json:"url"`
	Elements []DistilledElement `json:"elements"`
}

// DOMDistillerScript returns the JavaScript to inject into a page to extract
// all interactive elements and assign stable NAVIG handles to them.
// The script is designed to be run via chromedp Eval or playwright evaluate.
func DOMDistillerScript() string {
	return `(function() {
  var INTERACTIVE = 'a[href], button, input:not([type=hidden]), ' +
    'select, textarea, [role=button], [role=link], [role=menuitem], ' +
    '[role=option], [role=checkbox], [role=radio], [role=tab], ' +
    '[role=textbox], [tabindex]:not([tabindex="-1"])';

  var elements = Array.from(document.querySelectorAll(INTERACTIVE));
  var results = [];
  var handleAttr = 'data-navig-handle';

  elements.forEach(function(el, idx) {
    // Assign or reuse stable handle
    if (!el.getAttribute(handleAttr)) {
      el.setAttribute(handleAttr, 'navig-' + Math.random().toString(36).substr(2, 8));
    }
    var handle = el.getAttribute(handleAttr);

    var rect = el.getBoundingClientRect();
    var text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().substring(0, 60);

    results.push({
      index: idx + 1,
      tag: el.tagName.toLowerCase(),
      type: el.type || undefined,
      text: text || undefined,
      placeholder: el.placeholder || undefined,
      href: el.href || undefined,
      value: (el.value || '').substring(0, 40) || undefined,
      id: el.id || undefined,
      name: el.name || undefined,
      handle: handle,
      rect: {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        right: Math.round(rect.right),
        bottom: Math.round(rect.bottom)
      }
    });
  });

  return JSON.stringify({
    title: document.title,
    url: window.location.href,
    elements: results
  });
})()`
}

// ParseDistilledTree parses the JSON result from DOMDistillerScript.
func ParseDistilledTree(jsonRaw json.RawMessage) (*DistilledPage, error) {
	// The result is a JSON-encoded string (the eval result is a string)
	var jsonStr string
	if err := json.Unmarshal(jsonRaw, &jsonStr); err != nil {
		// Try direct decode if eval returns an object
		var page DistilledPage
		if err2 := json.Unmarshal(jsonRaw, &page); err2 != nil {
			return nil, fmt.Errorf("dom_distiller: parse failed: %w", err)
		}
		return &page, nil
	}
	var page DistilledPage
	if err := json.Unmarshal([]byte(jsonStr), &page); err != nil {
		return nil, fmt.Errorf("dom_distiller: parse inner JSON: %w", err)
	}
	return &page, nil
}

// ToMarkdown converts a DistilledPage into a compact numbered Markdown tree
// suitable for direct LLM consumption. The LLM emits {"action":"click","target":N}
// to reference element [N].
func (p *DistilledPage) ToMarkdown() string {
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# Page: \"%s\"\n", p.Title))
	sb.WriteString(fmt.Sprintf("> URL: %s\n\n", p.URL))

	for _, el := range p.Elements {
		var parts []string

		// Build human-readable description
		label := el.Text
		if label == "" {
			label = el.Placeholder
		}
		if label == "" && el.Href != "" {
			label = el.Href
		}
		if label == "" {
			label = el.ID
		}
		if label == "" {
			label = el.Name
		}
		if label == "" {
			label = "(no label)"
		}

		tag := el.Tag
		if el.Type != "" {
			tag = tag + "[" + el.Type + "]"
		}

		parts = append(parts, fmt.Sprintf("[%d] %s \"%s\"", el.Index, tag, label))

		if el.Value != "" {
			parts = append(parts, fmt.Sprintf("value=%q", el.Value))
		}

		if el.Rect.Left+el.Rect.Top > 0 {
			parts = append(parts, fmt.Sprintf("(rect: %d,%d-%d,%d)", el.Rect.Left, el.Rect.Top, el.Rect.Right, el.Rect.Bottom))
		}

		// Append stable handle as HTML comment (invisible to LLM reasoning, used for execution)
		parts = append(parts, fmt.Sprintf("<!-- handle:%s -->", el.Handle))

		sb.WriteString(strings.Join(parts, " ") + "\n")
	}

	return sb.String()
}

// ResolveHandle finds the NAVIG handle for element at the given index.
// Returns ("", false) if not found.
func (p *DistilledPage) ResolveHandle(index int) (handle string, found bool) {
	for _, el := range p.Elements {
		if el.Index == index {
			return el.Handle, true
		}
	}
	return "", false
}
