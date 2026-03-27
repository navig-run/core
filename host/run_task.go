package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/chromedp/chromedp"

	"navig-core/host/internal/browseragent/pageintel"
	"navig-core/host/internal/browseragent/scriptengine"
	"navig-core/host/internal/vault"
)

type TaskPayload struct {
	ID     string              `json:"id"`
	Intent string              `json:"intent"`
	Steps  []scriptengine.Step `json:"steps"`
}

func main() {
	// 1. Read the JSON Task
	data, err := os.ReadFile("./example-app_test.json")
	if err != nil {
		log.Fatalf("Could not read task: %v", err)
	}

	var payload TaskPayload
	if err := json.Unmarshal(data, &payload); err != nil {
		log.Fatalf("Failed to parse JSON AST: %v", err)
	}

	fmt.Println("========================================================================")
	fmt.Println("🚀 NAVIG COGNITIVE ENGINE: Booting Auto-All Execution from JSON...")
	fmt.Println("========================================================================")
	fmt.Printf("Task ID: %s\n", payload.ID)
	fmt.Printf("Intent:  %s\n\n", payload.Intent)

	// 2. Mocking the Secure Vault context (Simulating decrypted IPC payload from Python)
	// We inject "REMOVED:REMOVED" into memory for pageintel to consume natively.
	operatorVault := &vault.Vault{
		Accounts: []vault.AccountProfile{
			{Domain: "example.org", Username: "REMOVED", Password: "REMOVED"},
		},
	}

	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.Flag("headless", false),
	)
	allocCtx, cancel := chromedp.NewExecAllocator(context.Background(), opts...)
	defer cancel()

	ctx, cancelCtx := chromedp.NewContext(allocCtx)
	defer cancelCtx()
	ctx, cancelCtx = context.WithTimeout(ctx, 30*time.Second)
	defer cancelCtx()

	// 3. Inject our NavFn and EvalFn into the Adaptive Engine
	navFn := func(url string) error {
		fmt.Printf("[ACT] Navigating to: %s\n", url)
		return chromedp.Run(ctx, chromedp.Navigate(url))
	}
	evalFn := func(js string) ([]byte, error) {
		var res []byte
		err := chromedp.Run(ctx, chromedp.Evaluate(js, &res))
		return res, err
	}
	shotFn := func(path string) error {
		var buf []byte
		err := chromedp.Run(ctx, chromedp.CaptureScreenshot(&buf))
		if err == nil {
			_ = os.WriteFile(path, buf, 0644)
		}
		return err
	}
	emitter := func(event string, payload interface{}) {
		b, _ := json.Marshal(payload)
		fmt.Printf("[IPC EVENT] %s: %s\n", event, string(b))
	}

	// 4. Instantiating the Engine
	agent := scriptengine.New(evalFn, navFn, shotFn, emitter)

	// Since we use the new auto-login flow which looks directly at operatorVault
	// we will map the vault matches inside the execution flow.
	// To do this seamlessly inside `runStep.login`, `scriptengine.Run` needs credentials override
	// but PageIntel automatically matches vault inside native `smartFill` or `vaultMatch`.
	// For today's demo, the step is "login". The engine extracts the domain and dynamically calls PageIntel.
	// We inject Vault directly into the Credentials object so it knows the passwords.

	// Dynamically prepare creds if there are any for this run.
	var creds *pageintel.Credentials
	if len(operatorVault.Accounts) > 0 {
		c := &pageintel.Credentials{
			Username: operatorVault.Accounts[0].Username,
			Password: operatorVault.Accounts[0].Password,
		}
		creds = c
		fmt.Printf("[SYS] Secure IPC Vault Payload Loaded for Domain: %s\n", operatorVault.Accounts[0].Domain)
	}

	// 5. Fire the exact AST into Engine.Run()
	result, err := agent.Run(payload.Steps, creds)
	if err != nil {
		log.Fatalf("\n❌ Engine Critical Failure: %v", err)
	}

	fmt.Printf("\n✅ Execution Completed in %dms. Passed: %d, Failed: %d\n", result.DurationMs, result.Succeeded, result.Failed)
}
