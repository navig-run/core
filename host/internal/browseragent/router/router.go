package router

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"

	"navig-core/host/internal/browser"
	"navig-core/host/internal/browseragent"
	"navig-core/host/internal/browseragent/ipc"
	"navig-core/host/internal/browseragent/pageintel"
	"navig-core/host/internal/browseragent/profilemgr"
)

type Router struct {
	registry   *profilemgr.Registry
	cdpFactory browser.EngineFactory
	pwFactory  browser.EngineFactory
}

func New(reg *profilemgr.Registry, cdp browser.EngineFactory, pw browser.EngineFactory) *Router {
	return &Router{
		registry:   reg,
		cdpFactory: cdp,
		pwFactory:  pw,
	}
}

func (r *Router) ExecuteTask(ctx context.Context, req browser.TaskRunRequest, emitter ipc.Emitter, allowFallback bool, cloneOnBusy bool) (*browser.TaskRunResponse, error) {
	policy := RouterPolicy{
		AllowFallbackOnTimeout: allowFallback,
		CloneOnBusy:            cloneOnBusy,
	}
	// 1. Resolve Profile
	profID := profilemgr.ProfileID(req.Routing.Profile)
	if profID == "auto" || profID == "" {
		profID = "crypto" // Default per example or auto
	}

	prof, ok := r.registry.GetProfile(profID)
	if !ok {
		// Create an ephemeral profile record in memory if not in registry
		prof = profilemgr.ProfileRecord{
			ID:               profID,
			Dir:              filepath.Join(os.TempDir(), "navig_profile_"+string(profID)),
			PreferredEngine:  "navbrowser",
			PreferredBrowser: "auto",
		}
	}

	// 2. Lock Profile
	lock := profilemgr.NewLock(prof.Dir)
	if err := lock.Acquire(); err != nil {
		if err.Error() == "PROFILE_IN_USE" {
			if policy.CloneOnBusy {
				// Copy-on-Write / Fast Clone logic
				originalDir := prof.Dir
				prof.Dir = filepath.Join(os.TempDir(), "navig_profile_"+string(profID)+"_clone_"+uuid.New().String())

				// Perform actual filesystem fast-copy
				profilemgr.CloneProfile(originalDir, prof.Dir)

				lock = profilemgr.NewLock(prof.Dir)
				if err := lock.Acquire(); err != nil {
					return nil, err
				}
			} else {
				return nil, err
			}
		} else {
			return nil, err
		}
	}
	defer lock.Release()

	// 3. Resolve Engine
	engineName := ResolveEngine(req, prof)
	browserName := ResolveBrowser(req, prof)

	var driver browser.Driver
	if engineName == "playwright" {
		driver = r.pwFactory(emitter)
		// Check availability via ListBrowsers (side-effect-free — no Chrome launched).
		// The playwright stub returns ErrEngineNotInstalled; navbrowser returns nil.
		if _, probeErr := driver.ListBrowsers(); probeErr == browser.ErrEngineNotInstalled {
			if !policy.AllowFallbackOnTimeout {
				emitter.Lifecycle(ipc.EventCtx{}, ipc.LifecycleData{Event: "EngineUnavailable"})
				return nil, &EngineUnavailableError{
					Err:     "engine_unavailable",
					Engine:  "playwright",
					Message: "playwright driver not installed",
				}
			}
			emitter.Lifecycle(ipc.EventCtx{}, ipc.LifecycleData{
				Event:    "DriverFallback",
				Fallback: "chromedp",
			})
			engineName = "chromedp"
			driver = r.cdpFactory(emitter)
		}
	} else {
		// auto or chromedp — cdpFactory always available, no probe needed
		driver = r.cdpFactory(emitter)
	}

	// 4. Launch Session
	evCtx := ipc.EventCtx{}
	emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: "Launch"})

	start := time.Now()

	session, err := driver.Launch(browser.SessionLaunchConfig{
		ProfileName: browser.ProfileName(prof.Dir), // Assuming dir is passed here
		BrowserName: browser.BrowserName(browserName),
		Headless:    req.Routing.Headless,
		Args:        []string{},
		DriverType:  engineName,
	})
	if err != nil {
		return nil, err
	}
	defer driver.Close(browser.CloseSessionConfig{SessionId: session.SessionId})

	// 5. Artifact directories
	home, _ := os.UserHomeDir()
	artDir := filepath.Join(home, ".navig", "browser", "artifacts", req.TaskID)
	os.MkdirAll(artDir, 0700)

	htmlPath := filepath.Join(artDir, "page.html")
	_ = htmlPath // reserved for future page.html dump; not yet written by driver
	jsonPath := filepath.Join(artDir, "page.json")

	// makeEvalFn wraps driver.Eval into the pageintel.EvalFn signature
	// so pageintel Inspector can be used directly in the step loop.
	makeEvalFn := func(pageId string) pageintel.EvalFn {
		return func(js string) ([]byte, error) {
			res, err := driver.Eval(browser.EvalConfig{PageId: pageId, Js: js, TimeoutMs: 8000})
			if err != nil {
				return nil, err
			}
			return res.Result, nil
		}
	}

	// makeNavFn wraps driver.Goto for use in pageintel login flow.
	makeNavFn := func(pageId string) func(string) error {
		return func(url string) error {
			_, err := driver.Goto(browser.GotoConfig{PageId: pageId, Url: url})
			return err
		}
	}

	// 6. Execute Steps — full action loop
	page, err := driver.NewTab(browser.NewTabConfig{SessionId: session.SessionId})
	if err != nil {
		return nil, err
	}

	evCtx.SessionID = session.SessionId
	evCtx.PageID = page.PageId

	var finalURL string
	var finalTitle string
	var screenshotPaths []string
	var domMarkdown string

	// helperFillBySelector: fills a text input via JS eval (selector-based)
	fillBySelector := func(selector, value string) error {
		js := fmt.Sprintf(`(function(){
			var el = document.querySelector(%q);
			if (!el) return 'not_found';
			el.focus();
			var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
			nativeInputValueSetter.call(el, %q);
			el.dispatchEvent(new Event('input', {bubbles:true}));
			el.dispatchEvent(new Event('change', {bubbles:true}));
			return 'ok';
		})()`, selector, value)
		res, err := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 5000})
		if err != nil {
			return fmt.Errorf("fill %q: %w", selector, err)
		}
		_ = res
		return nil
	}

	// helperClickBySelector: clicks an element via JS eval
	clickBySelector := func(selector string) error {
		js := fmt.Sprintf(`(function(){
			var el = document.querySelector(%q);
			if (!el) return 'not_found';
			el.click();
			return 'ok';
		})()`, selector)
		res, err := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 5000})
		if err != nil {
			return fmt.Errorf("click %q: %w", selector, err)
		}
		_ = res
		return nil
	}

	// detectNeedsHuman: scan the DOM title/body for signals that a human is required
	detectNeedsHuman := func() string {
		js := `(function(){
			var body = document.body ? document.body.innerText.toLowerCase() : '';
			var title = document.title.toLowerCase();
			var combined = title + ' ' + body.substring(0, 1000);
			if (/captcha|robot|verify you are human|i'm not a robot/.test(combined)) return 'captcha';
			if (/two.factor|2fa|authentication code|verification code|enter code|confirm your identity/.test(combined)) return '2fa';
			if (/403 forbidden|access denied|your account has been blocked/.test(combined)) return 'blocked';
			return '';
		})()`
		res, err := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 3000})
		if err != nil {
			return ""
		}
		var s string
		_ = json.Unmarshal(res.Result, &s)
		return s
	}

	for i, step := range req.Steps {

		stepEmit := func(event, url string) {
			emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: event, URL: url})
		}

		switch {

		case step.Goto != nil:
			stepEmit("Navigate", step.Goto.URL)
			res, err := driver.Goto(browser.GotoConfig{PageId: page.PageId, Url: step.Goto.URL})
			if err != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "NAV_ERROR", Message: err.Error(), Retryable: true})
				if !policy.AllowFallbackOnTimeout {
					return nil, err
				}
			}
			if res != nil {
				finalURL = res.Url
				finalTitle = res.Title
			}
			// Check if the page requires human interaction
			if signal := detectNeedsHuman(); signal != "" {
				emitter.Status(evCtx, "warn", ipc.StatusData{
					Phase:   "needs_human",
					Message: fmt.Sprintf("Page requires human: %s", signal),
				})
				// Signal is embedded in the response for the Python layer to pick up
				// and route to the Telegram bridge (Phase 4)
				return &browser.TaskRunResponse{
					FinalURL:    finalURL,
					Title:       finalTitle,
					EngineUsed:  engineName,
					BrowserUsed: browserName,
					ProfileUsed: req.Routing.Profile,
					NeedsHuman:  signal, // e.g. "captcha" or "2fa"
					Lifecycle: struct {
						StepCount  int   `json:"stepCount"`
						DurationMs int64 `json:"durationMs"`
					}{StepCount: i + 1, DurationMs: time.Since(start).Milliseconds()},
				}, nil
			}

		case step.Login != nil:
			stepEmit("Login", step.Login.URL)

			// Navigate to the login URL first
			if step.Login.URL != "" {
				if _, err := driver.Goto(browser.GotoConfig{PageId: page.PageId, Url: step.Login.URL}); err != nil {
					emitter.Error(evCtx, ipc.ErrorData{Code: "LOGIN_NAV_ERROR", Message: err.Error(), Retryable: true})
					if !policy.AllowFallbackOnTimeout {
						return nil, err
					}
				}
			}

			// Build credentials — try vault first, then direct fields
			var creds pageintel.Credentials
			if step.Login.CredentialID != "" {
				usr, pwd, vaultErr := resolveVaultCredential(step.Login.CredentialID)
				if vaultErr != nil {
					emitter.Error(evCtx, ipc.ErrorData{Code: "VAULT_ERROR", Message: vaultErr.Error(), Retryable: false})
				} else {
					creds.Username = usr
					creds.Password = pwd
				}
			} else {
				creds.Username = step.Login.Username
				// Note: bare-password in step JSON is intentionally NOT supported.
				// Use credential_id (vault) for all production use cases.
			}

			// Run pageintel LoginFlow — auto-detects fields, fills, submits, detects outcome
			inspector := pageintel.New(makeEvalFn(page.PageId))
			outcome, loginErr := inspector.LoginFlow(creds, makeNavFn(page.PageId))
			if loginErr != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "LOGIN_ERROR", Message: loginErr.Error(), Retryable: true})
				if !policy.AllowFallbackOnTimeout {
					return nil, loginErr
				}
			} else {
				if outcome.NeedsHuman != "" {
					emitter.Status(evCtx, "warn", ipc.StatusData{
						Phase:   "needs_human",
						Message: fmt.Sprintf("Login blocked: %s", outcome.NeedsHuman),
					})
					return &browser.TaskRunResponse{
						FinalURL: finalURL, Title: finalTitle,
						EngineUsed: engineName, BrowserUsed: browserName, ProfileUsed: req.Routing.Profile,
						NeedsHuman: outcome.NeedsHuman,
						Lifecycle: struct {
							StepCount  int   `json:"stepCount"`
							DurationMs int64 `json:"durationMs"`
						}{StepCount: i + 1, DurationMs: time.Since(start).Milliseconds()},
					}, nil
				}
				if !outcome.Success {
					emitter.Error(evCtx, ipc.ErrorData{Code: "LOGIN_FAILED", Message: outcome.ErrorMsg, Retryable: true})
				} else {
					emitter.Status(evCtx, "info", ipc.StatusData{Phase: "login", Message: "Login successful"})
				}
			}

		case step.Analyze != nil:
			stepEmit("Analyze", "")
			inspector := pageintel.New(makeEvalFn(page.PageId))
			analysis, analyzeErr := inspector.Analyze()
			if analyzeErr != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "ANALYZE_ERROR", Message: analyzeErr.Error(), Retryable: true})
			} else {
				if step.Analyze.SaveToArtifact {
					if b, err := json.Marshal(analysis); err == nil {
						analyzePath := filepath.Join(artDir, fmt.Sprintf("analyze_%d.json", i+1))
						_ = os.WriteFile(analyzePath, b, 0600)
						emitter.Artifact(evCtx, ipc.ArtifactData{Kind: "page_analysis", Path: analyzePath})
					}
				}
				emitter.Status(evCtx, "info", ipc.StatusData{
					Phase:   "analyze",
					Message: fmt.Sprintf("Page analyzed: type=%s inputs=%d captcha=%v", analysis.PageType, len(analysis.Inputs), analysis.HasCaptcha),
				})
			}

		case step.DetectOutcome != nil:
			stepEmit("DetectOutcome", "")
			inspector := pageintel.New(makeEvalFn(page.PageId))
			outcome, outErr := inspector.DetectOutcome()
			if outErr != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "OUTCOME_ERROR", Message: outErr.Error(), Retryable: true})
			} else {
				if step.DetectOutcome.SaveToArtifact {
					if b, err := json.Marshal(outcome); err == nil {
						outPath := filepath.Join(artDir, fmt.Sprintf("outcome_%d.json", i+1))
						_ = os.WriteFile(outPath, b, 0600)
						emitter.Artifact(evCtx, ipc.ArtifactData{Kind: "outcome", Path: outPath})
					}
				}
				emitter.Status(evCtx, "info", ipc.StatusData{
					Phase:   "detect_outcome",
					Message: fmt.Sprintf("Outcome: %s — %s", outcome.Status, outcome.Detail),
				})
					if outcome.Status == "captcha" || outcome.Status == "mfa" || outcome.Status == "blocked" {
						return &browser.TaskRunResponse{
							FinalURL: outcome.URL, Title: outcome.Title,
							EngineUsed: engineName, BrowserUsed: browserName, ProfileUsed: req.Routing.Profile,
							NeedsHuman: string(outcome.Status),
							Lifecycle: struct {
								StepCount  int   `json:"stepCount"`
								DurationMs int64 `json:"durationMs"`
							}{StepCount: i + 1, DurationMs: time.Since(start).Milliseconds()},
						}, nil
					}
			}

		case step.Click != nil:
			stepEmit("Click", step.Click.Target)
			if err := clickBySelector(step.Click.Target); err != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "CLICK_ERROR", Message: err.Error(), Retryable: true})
			}

		case step.Fill != nil:
			stepEmit("Fill", step.Fill.Target)
			if err := fillBySelector(step.Fill.Target, step.Fill.Value); err != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "FILL_ERROR", Message: err.Error(), Retryable: true})
			}

		case step.VaultFill != nil:
			// The Python vault API is called via IPC to fetch the credential.
			// Credentials are never written to any log.
			stepEmit("VaultFill", step.VaultFill.CredentialID)
			vf := step.VaultFill

			username, password, vaultErr := resolveVaultCredential(vf.CredentialID)
			if vaultErr != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "VAULT_ERROR", Message: vaultErr.Error(), Retryable: false})
			} else {
				if vf.UsernameSelector != "" && username != "" {
					_ = fillBySelector(vf.UsernameSelector, username)
				}
				if vf.PasswordSelector != "" && password != "" {
					_ = fillBySelector(vf.PasswordSelector, password)
				}
				if vf.SubmitSelector != "" {
					_ = clickBySelector(vf.SubmitSelector)
					time.Sleep(500 * time.Millisecond) // allow nav
				}
			}

		case step.GetDOM != nil:
			stepEmit("GetDOM", "")
			res, err := driver.Eval(browser.EvalConfig{
				PageId:    page.PageId,
				Js:        browseragent.DOMDistillerScript(),
				TimeoutMs: 8000,
			})
			if err != nil {
				emitter.Error(evCtx, ipc.ErrorData{Code: "GETDOM_ERROR", Message: err.Error(), Retryable: true})
			} else {
				page, parseErr := browseragent.ParseDistilledTree(res.Result)
				if parseErr == nil {
					domMarkdown = page.ToMarkdown()
					if step.GetDOM.SaveToArtifact {
						domPath := filepath.Join(artDir, "dom.md")
						_ = os.WriteFile(domPath, []byte(domMarkdown), 0600)
						emitter.Artifact(evCtx, ipc.ArtifactData{Kind: "dom_tree", Path: domPath})
					}
				}
			}

		case step.Wait != nil:
			stepEmit("Wait", step.Wait.Kind)
			switch step.Wait.Kind {
			case "delay_ms":
				if step.Wait.DelayMs > 0 {
					time.Sleep(time.Duration(step.Wait.DelayMs) * time.Millisecond)
				}
			case "selector":
				// Poll for selector up to timeout
				timeout := time.Duration(step.Wait.TimeoutMs) * time.Millisecond
				if timeout == 0 {
					timeout = 10 * time.Second
				}
				deadline := time.Now().Add(timeout)
				for time.Now().Before(deadline) {
					js := fmt.Sprintf(`!!document.querySelector(%q)`, step.Wait.Selector)
					res, _ := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 1000})
					var found bool
					if res != nil {
						_ = json.Unmarshal(res.Result, &found)
					}
					if found {
						break
					}
					time.Sleep(300 * time.Millisecond)
				}
			default:
				// dom_ready: just wait for eval that returns document.readyState
				js := `document.readyState`
				for attempt := 0; attempt < 20; attempt++ {
					res, _ := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 1000})
					var state string
					if res != nil {
						_ = json.Unmarshal(res.Result, &state)
					}
					if state == "complete" || state == "interactive" {
						break
					}
					time.Sleep(200 * time.Millisecond)
				}
			}

		case step.Screenshot != nil:
			path := step.Screenshot.Path
			if path == "" {
				path = filepath.Join(artDir, fmt.Sprintf("screenshot_%d.png", i+1))
			}
			_, _ = driver.Screenshot(browser.ScreenshotConfig{PageId: page.PageId, Path: path})
			screenshotPaths = append(screenshotPaths, path)
			emitter.Artifact(evCtx, ipc.ArtifactData{Kind: "screenshot", Path: path})

		case step.Eval != nil:
			stepEmit("Eval", "")
			_, _ = driver.Eval(browser.EvalConfig{
				PageId:    page.PageId,
				Js:        step.Eval.JS,
				TimeoutMs: 10000,
			})

		case step.Extract != nil:
			stepEmit("Extract", step.Extract.Kind)
			var js string
			switch step.Extract.Kind {
			case "text":
				js = `document.body ? document.body.innerText : ''`
			case "links":
				js = `JSON.stringify(Array.from(document.querySelectorAll('a[href]')).map(a=>({text:a.innerText.trim(),href:a.href})))`
			case "dom_tree":
				js = browseragent.DOMDistillerScript()
			default:
				js = `document.documentElement.outerHTML`
			}
			res, err := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: js, TimeoutMs: 8000})
			if err == nil && res != nil {
				extractPath := filepath.Join(artDir, fmt.Sprintf("extract_%d_%s.json", i+1, step.Extract.Kind))
				_ = os.WriteFile(extractPath, res.Result, 0600)
				emitter.Artifact(evCtx, ipc.ArtifactData{Kind: "extract", Path: extractPath})
			}
		}
	}

	// 7. Save artifacts — real live page data
	// Always capture a final screenshot representing the terminal page state.
	finalShot := filepath.Join(artDir, "final_screenshot.png")
	_, _ = driver.Screenshot(browser.ScreenshotConfig{PageId: page.PageId, Path: finalShot})
	screenshotPaths = append(screenshotPaths, finalShot)

	// Fetch final page title + URL if not already set
	if finalTitle == "" {
		res, err := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: `document.title`, TimeoutMs: 3000})
		if err == nil && res != nil {
			_ = json.Unmarshal(res.Result, &finalTitle)
		}
	}
	if finalURL == "" {
		res, err := driver.Eval(browser.EvalConfig{PageId: page.PageId, Js: `window.location.href`, TimeoutMs: 3000})
		if err == nil && res != nil {
			_ = json.Unmarshal(res.Result, &finalURL)
		}
	}

	// Save DOM markdown if generated
	if domMarkdown != "" {
		_ = os.WriteFile(filepath.Join(artDir, "dom.md"), []byte(domMarkdown), 0600)
	}

	// Write page.json with live data
	pageJSON := fmt.Sprintf(`{"title":%q,"url":%q,"capturedAt":%q}`, finalTitle, finalURL, time.Now().Format(time.RFC3339))
	_ = os.WriteFile(jsonPath, []byte(pageJSON), 0600)

	emitter.Lifecycle(evCtx, ipc.LifecycleData{Event: "TaskCompleted"})

	return &browser.TaskRunResponse{
		Artifacts: struct {
			ScreenshotPaths []string `json:"screenshotPaths"`
			HTMLDumpPath    string   `json:"htmlDumpPath"`
			LogPath         string   `json:"logPath"`
		}{
			ScreenshotPaths: screenshotPaths,
			HTMLDumpPath:    htmlPath,
			LogPath:         filepath.Join(artDir, "run.log"),
		},
		FinalURL:    finalURL,
		Title:       finalTitle,
		EngineUsed:  engineName,
		BrowserUsed: browserName,
		ProfileUsed: req.Routing.Profile,
		Lifecycle: struct {
			StepCount  int   `json:"stepCount"`
			DurationMs int64 `json:"durationMs"`
		}{
			StepCount:  len(req.Steps),
			DurationMs: time.Since(start).Milliseconds(),
		},
	}, nil
}

// resolveVaultCredential looks up a credential from the Python NAVIG vault
// via a local HTTP call to the running host daemon API.
// Returns username, password. Secret values are never logged.
func resolveVaultCredential(credentialID string) (username, password string, err error) {
	// Vault resolution is done via a loopback HTTP call to the navig-host daemon API.
	// The daemon proxies to the Python vault via its internal IPC.
	// This avoids importing Python crypto libs into Go.
	resp, err := http.Get(fmt.Sprintf("http://127.0.0.1:7421/api/v1/vault/credential/%s/resolve", credentialID))
	if err != nil {
		return "", "", fmt.Errorf("vault resolve: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", "", fmt.Errorf("vault resolve: HTTP %d", resp.StatusCode)
	}
	var result struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", "", fmt.Errorf("vault resolve: decode: %w", err)
	}
	return result.Username, result.Password, nil
}
