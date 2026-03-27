package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"time"
)

// simulateTelegramPush renders a beautiful ANSI box representing the native mobile push notification
func simulateTelegramPush(target string, body string, price string) {
	fmt.Println("\n\033[36m📲 TELEGRAM (NAVIG BOT PUSH NOTIFICATION)\033[0m")
	fmt.Println("╭───────────────────────────────────────────────────╮")
	fmt.Println("│ \033[1;33m⚠️ NAVIG CHECKPOINT: PAYMENT REQUIRED\033[0m             │")
	fmt.Println("├───────────────────────────────────────────────────┤")
	fmt.Printf("│ \033[1mTarget\033[0m:  %-41s │\n", target)
	fmt.Printf("│ \033[1mAction\033[0m:  %-41s │\n", body)
	if price != "" {
		fmt.Printf("│ \033[1mAmount\033[0m:  \033[1;31m%s\033[0m%-36s │\n", price, "")
	}
	fmt.Println("├───────────────────────────────────────────────────┤")
	fmt.Println("│ \033[1;32m[ /approve ]\033[0m        \033[1;31m[ /abort ]\033[0m                    │")
	fmt.Println("╰───────────────────────────────────────────────────╯")
}

// simulateBridgeVscodeNotification renders what the VS Code Extension or CLI sees
func simulateBridgeVscodeNotification(target string, action string) {
	fmt.Println("\n\033[34m[NAVIG BRIDGE EXTENSION (VS CODE ALERT)]\033[0m")
	fmt.Printf("🔔 The Cognitive Engine halted on \033[1m%s\033[0m.\n", target)
	fmt.Printf("   Awaiting Operator consent to \033[1m%s\033[0m.\n", action)
	fmt.Printf("   \033[2m(Approve via CLI, VS Code Command Palette, or Telegram to resume execution)\033[0m\n")
}

func startMockWebhookServer(approveChan chan bool) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/approve", func(w http.ResponseWriter, r *http.Request) {
		approveChan <- true
		fmt.Fprintf(w, "<html><body style='font-family: sans-serif; text-align: center; margin-top: 50px;'>")
		fmt.Fprintf(w, "<h1 style='color: green;'>✅ Transaction Approved!</h1>")
		fmt.Fprintf(w, "<p>The NAVIG Engine has received your Telegram/Webhook signal.</p>")
		fmt.Fprintf(w, "<p>You can close this window.</p></body></html>")
	})
	mux.HandleFunc("/abort", func(w http.ResponseWriter, r *http.Request) {
		approveChan <- false
		fmt.Fprintf(w, "<html><body style='font-family: sans-serif; text-align: center; margin-top: 50px;'>")
		fmt.Fprintf(w, "<h1 style='color: red;'>❌ Transaction Aborted.</h1>")
		fmt.Fprintf(w, "<p>The NAVIG Engine has gracefully closed the session.</p>")
		fmt.Fprintf(w, "<p>You can close this window.</p></body></html>")
	})

	srv := &http.Server{Addr: ":8088", Handler: mux}
	go func() {
		if err := srv.ListenAndServe(); err != http.ErrServerClosed {
			log.Fatalf("Mock HTTP Server Error: %v", err)
		}
	}()
	return srv
}

func main() {
	approveChan := make(chan bool)
	webhookServer := startMockWebhookServer(approveChan)
	defer webhookServer.Shutdown(context.Background())

	fmt.Println("========================================================================")
	fmt.Println("🚀 NAVIG COGNITIVE OODA LOOP: TELEGRAM & BRIDGE UI DEMO")
	fmt.Println("========================================================================")
	fmt.Println("Executing task: ID: tx-019a")

	fmt.Println("\n[OBSERVE] Analyzing checkout DOM structure...")
	time.Sleep(500 * time.Millisecond)
	fmt.Println("[ACT]     PageIntel.VaultMatch activated! Natively injecting encrypted Operator details (0.2s)...")
	time.Sleep(500 * time.Millisecond)
	fmt.Println("[DECIDE]  Next intent is 'Commit Payment'. Point-of-No-Return triggered.")
	fmt.Println("[ACT]     scriptengine.RequestOperatorCheckpoint() fired.")
	time.Sleep(500 * time.Millisecond)

	// Triggering the notifications exactly as they appear in the user's workflow
	simulateBridgeVscodeNotification("example.org", "Purchase 1 Year Premium ($50.00)")
	simulateTelegramPush("example.org", "Purchase 1 Year Premium", "$50.00")

	fmt.Println("\n>> Engine suspended JS execution on page. Awaiting Operator ACK.")
	fmt.Println("\033[35m>> For Webhook/Telegram Simulation, CTRL+Click this link to Approve: \033[4;34mhttp://localhost:8088/approve\033[0m")

	// Blocks execution forever until we hit the HTTP route
	approved := <-approveChan

	if approved {
		fmt.Println("\n\033[1;32m[IPC RECEIVED] Telegram / Webhook Webhook Signal Received: APPROVED.\033[0m")
		fmt.Println("\033[1;32m[ACT]          NavBrowser.Click('form#checkout .submit'); Resuming OODA Loop...\033[0m")
	} else {
		fmt.Println("\n\033[1;31m[IPC RECEIVED] Telegram / Webhook Signal: ABORT. Gracefully closing...\033[0m")
	}

	fmt.Println("\n✅ Execution Summary: Checkpoint integration test completed.")
}
