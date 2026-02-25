package browser

type TaskRunRequest struct {
	TaskID string `json:"taskId"`
	Intent string `json:"intent"` // login_check | crawl_page | extract_dom | purchase_flow
	Target struct {
		URL    string `json:"url"`
		Domain string `json:"domain,omitempty"`
	} `json:"target"`
	Routing struct {
		Profile  string `json:"profile"` // crypto | social | work | auto
		Engine   string `json:"engine"`  // auto | chromedp | playwright
		Browser  string `json:"browser"` // auto | chrome | edge
		Headless bool   `json:"headless"`
	} `json:"routing"`
	Steps []TaskStep `json:"steps"`
}

type TaskStep struct {
	Goto          *StepGoto          `json:"goto,omitempty"`
	Click         *StepClick         `json:"click,omitempty"`
	Fill          *StepFill          `json:"fill,omitempty"`
	VaultFill     *StepVaultFill     `json:"vault_fill,omitempty"`
	Login         *StepLogin         `json:"login,omitempty"`
	GetDOM        *StepGetDOM        `json:"get_dom,omitempty"`
	Wait          *StepWait          `json:"wait,omitempty"`
	Eval          *StepEval          `json:"eval,omitempty"`
	Screenshot    *StepScreenshot    `json:"screenshot,omitempty"`
	Extract       *StepExtract       `json:"extract,omitempty"`
	Analyze       *StepAnalyze       `json:"analyze,omitempty"`
	DetectOutcome *StepDetectOutcome `json:"detect_outcome,omitempty"`
	CognitiveFill *StepCognitiveFill `json:"cognitive_fill,omitempty"`
}

type StepCognitiveFill struct {
	Intent  string `json:"intent"`
	Payload string `json:"payload,omitempty"`
	Submit  bool   `json:"submit,omitempty"`
}

type StepGoto struct {
	URL string `json:"url"`
}

// StepClick clicks a DOM element. Target is a CSS selector or navig handle.
// Text provides a fallback: match by visible button text when selector fails.
type StepClick struct {
	Target string `json:"target"` // CSS selector or navig handle
	Text   string `json:"text,omitempty"` // fallback: match by visible text
}

// StepFill fills a text input. Target is a CSS selector or navig handle.
// Hint provides a semantic label ("email", "password", "search") used by the
// selector evolution engine when the primary Target fails.
type StepFill struct {
	Target string `json:"target"`
	Value  string `json:"value"` // plain text value (passwords come via vault_fill or login)
	Hint   string `json:"hint,omitempty"` // semantic hint for evolution: "email" | "username" | "search"
}

// StepVaultFill auto-fills credentials from the vault.
// The Go executor looks up the credential and injects username+password.
// Secret values are NEVER logged.
type StepVaultFill struct {
	CredentialID     string `json:"credential_id"`           // vault credential ID
	UsernameSelector string `json:"username_selector"`       // CSS selector for username field
	PasswordSelector string `json:"password_selector"`       // CSS selector for password field
	SubmitSelector   string `json:"submit_selector,omitempty"` // optional: click submit after fill
}

// StepGetDOM triggers the DOM distiller and stores the numbered Markdown tree
// as an artifact. Used for LLM reads before issuing click/fill commands.
type StepGetDOM struct {
	SaveToArtifact bool `json:"save_to_artifact"` // write dom.md to artifact dir
}

type StepWait struct {
	Kind      string `json:"kind"` // dom_ready | selector | network_idle | delay_ms
	Selector  string `json:"selector,omitempty"`
	TimeoutMs int    `json:"timeoutMs,omitempty"`
	DelayMs   int    `json:"delayMs,omitempty"`
}

type StepEval struct {
	JS string `json:"js"`
}

type StepScreenshot struct {
	Path string `json:"path,omitempty"`
}

type StepExtract struct {
	Kind string `json:"kind"` // outerHTML | text | links | dom_tree | table | list | meta | inputs
}

// StepLogin performs a fully-automated login sequence via pageintel.
// NavBrowser finds the username/password fields and submit button automatically —
// no selectors required. Credentials flow from vault_fill or direct IPC params.
type StepLogin struct {
	URL              string `json:"url"`
	CredentialID     string `json:"credential_id,omitempty"`     // vault lookup (preferred)
	Username         string `json:"username,omitempty"`           // direct (non-sensitive)
	// password is NEVER in the JSON — always read from vault or Credentials field
}

// StepAnalyze triggers a full DOM introspection via pageintel.
// Returns PageAnalysis: page type, forms, inputs with auto-generated selectors,
// buttons, error text, captcha/mfa flags. Saved as analyze.json artifact.
type StepAnalyze struct {
	SaveToArtifact bool `json:"save_to_artifact"`
}

// StepDetectOutcome inspects the page after an action and classifies what happened.
// Returns outcome: "success" | "error" | "captcha" | "mfa" | "blocked" | "redirect"
type StepDetectOutcome struct {
	SaveToArtifact bool `json:"save_to_artifact"`
}

type TaskRunResponse struct {
	Artifacts struct {
		ScreenshotPaths []string `json:"screenshotPaths"`
		HTMLDumpPath    string   `json:"htmlDumpPath"`
		LogPath         string   `json:"logPath"`
	} `json:"artifacts"`
	FinalURL    string `json:"finalUrl"`
	Title       string `json:"title"`
	EngineUsed  string `json:"engineUsed"`
	BrowserUsed string `json:"browserUsed"`
	ProfileUsed string `json:"profileUsed"`
	// NeedsHuman is set when the page requires human intervention before continuing.
	// Values: "captcha", "2fa", "blocked", "" (empty = no intervention needed)
	// The Python orchestration layer routes this to the Telegram bridge.
	NeedsHuman  string `json:"needsHuman,omitempty"`
	Lifecycle   struct {
		StepCount  int   `json:"stepCount"`
		DurationMs int64 `json:"durationMs"`
	} `json:"lifecycle"`
}

