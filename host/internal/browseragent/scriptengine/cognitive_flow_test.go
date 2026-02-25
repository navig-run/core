package scriptengine_test

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"navig-core/host/internal/browseragent/scriptengine"
	"navig-core/host/internal/vault"
)

// startMockStore spinning up a fake e-commerce checkout flow.
func startMockStore() *httptest.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, `
			<html>
				<body>
					<h1>Storefront</h1>
					<div class="product">
						<h2>Mechanical Keyboard</h2>
						<span class="price">$99.00</span>
						<a href="/checkout" id="buy-btn">Buy Now</a>
					</div>
				</body>
			</html>
		`)
	})
	mux.HandleFunc("/checkout", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, `
			<html>
				<body>
					<h1>Guest Checkout</h1>
					<form action="/confirm">
						<input type="text" name="fname" placeholder="First Name">
						<input type="text" name="lname" placeholder="Last Name">
						<input type="email" name="email" placeholder="Email Address">
						<button type="submit">Continue to Payment</button>
					</form>
				</body>
			</html>
		`)
	})
	mux.HandleFunc("/confirm", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, `
			<html>
				<body>
					<h1>Payment Required</h1>
					<p>Total: $99.00</p>
					<button id="pay-btn">Pay $99.00</button>
				</body>
			</html>
		`)
	})

	return httptest.NewServer(mux)
}

func TestCognitiveEngine_E2E_Checkout(t *testing.T) {
	// 1. Setup a Mock E-Commerce Server
	server := startMockStore()
	defer server.Close()

	// 2. Load the Mock Vault
	operatorVault := &vault.Vault{
		Identity: vault.Identity{
			FirstName: "John",
			LastName:  "Doe",
			Email:     "john@example.com",
		},
	}

	// 3. Issue the High-Level Intent (NO hardcoded CSS selectors!)
	intent := fmt.Sprintf("Buy the 'Mechanical Keyboard' from %s", server.URL)

	// 4. Run the engine asynchronously so we can intercept the checkpoint
	engine := scriptengine.NewAutonomousAgent(operatorVault)
	checkpointChan := make(chan scriptengine.CheckpointRequest)

	go engine.ExecuteIntent(intent, checkpointChan)

	// 5. Wait for the engine to reach the checkout screen (Point of No Return)
	var checkpoint scriptengine.CheckpointRequest
	select {
	case checkpoint = <-checkpointChan:
		t.Log("Checkpoint Reached. Halting execution for Operator.")
	case <-time.After(2 * time.Second):
		t.Fatal("Engine failed to reach checkpoint in time.")
	}

	// 6. Assertions: Did it find the right item? Did it calculate the price?
	if checkpoint.Summary.ItemName != "Mechanical Keyboard" {
		t.Errorf("Expected item 'Mechanical Keyboard', got '%s'", checkpoint.Summary.ItemName)
	}
	if checkpoint.Summary.TotalPrice != "$99.00" {
		t.Errorf("Expected price '$99.00', got '%s'", checkpoint.Summary.TotalPrice)
	}

	// 7. Simulate Human clicking "YES" (Approve)
	t.Log("Operator sends ACK: [Y] Approve")
	checkpoint.Approve()

	// 8. Verify final success state
	finalState := engine.WaitForCompletion()
	if !finalState.Success {
		t.Fatalf("Engine failed to complete after approval: %s", finalState.Reason)
	}
	t.Log("Execution Complete. Status: Success")
}
