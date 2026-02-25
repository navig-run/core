package router

import (
	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/profilemgr"
	"testing"
)

func TestResolveEngine(t *testing.T) {
	req := browser.TaskRunRequest{}
	req.Routing.Engine = "auto"

	prof := profilemgr.ProfileRecord{
		PreferredEngine: "auto",
	}

	if eng := ResolveEngine(req, prof); eng != "chromedp" {
		t.Errorf("Expected chromedp for auto/auto, got %s", eng)
	}

	req.Routing.Engine = "playwright"
	if eng := ResolveEngine(req, prof); eng != "playwright" {
		t.Errorf("Expected explicit playwright routing, got %s", eng)
	}

	req.Routing.Engine = "auto"
	prof.PreferredEngine = "playwright"
	if eng := ResolveEngine(req, prof); eng != "playwright" {
		t.Errorf("Expected profile preference playwright, got %s", eng)
	}
}

func TestResolveBrowser(t *testing.T) {
	req := browser.TaskRunRequest{}
	req.Routing.Browser = "auto"

	prof := profilemgr.ProfileRecord{
		PreferredBrowser: "edge",
	}

	if br := ResolveBrowser(req, prof); br != "edge" {
		t.Errorf("Expected edge based on profile preference, got %s", br)
	}

	req.Routing.Browser = "chrome"
	if br := ResolveBrowser(req, prof); br != "chrome" {
		t.Errorf("Expected explicit chrome over profile, got %s", br)
	}
}

func TestEngineUnavailableError(t *testing.T) {
	err := &EngineUnavailableError{
		Err:     "engine_unavailable",
		Engine:  "playwright",
		Message: "playwright driver not installed",
	}

	if err.Error() != `{"error":"engine_unavailable","engine":"playwright","message":"playwright driver not installed"}` {
		t.Errorf("JSON serialization of error failed, got %s", err.Error())
	}
}
