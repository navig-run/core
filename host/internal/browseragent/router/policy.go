package router

import (
	"encoding/json"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent/profilemgr"
)

type RouterPolicy struct {
	AllowFallbackOnTimeout bool
	CloneOnBusy            bool
}

type EngineUnavailableError struct {
	Err     string `json:"error"`
	Engine  string `json:"engine"`
	Message string `json:"message"`
}

func (e *EngineUnavailableError) Error() string {
	b, _ := json.Marshal(e)
	return string(b)
}

func ResolveEngine(req browser.TaskRunRequest, profile profilemgr.ProfileRecord) string {
	engine := req.Routing.Engine
	if engine == "chromedp" || engine == "playwright" {
		return engine
	}

	if profile.PreferredEngine == "chromedp" || profile.PreferredEngine == "playwright" {
		return profile.PreferredEngine
	}

	return "chromedp"
}

func ResolveBrowser(req browser.TaskRunRequest, profile profilemgr.ProfileRecord) string {
	b := req.Routing.Browser
	if b == "chrome" || b == "edge" {
		return b
	}

	if profile.PreferredBrowser == "chrome" || profile.PreferredBrowser == "edge" {
		return profile.PreferredBrowser
	}

	// Default fallback implemented during driver launch phase.
	return "auto"
}
