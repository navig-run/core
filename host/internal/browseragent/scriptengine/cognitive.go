package scriptengine

import (
	"time"

	"navig-core/host/internal/browseragent/ipc"
	"navig-core/host/internal/vault"
)

// TransactionSummary represents the structured data extracted at a point of no return.
type TransactionSummary struct {
	ItemName   string `json:"itemName"`
	TotalPrice string `json:"totalPrice"`
	Dates      string `json:"dates,omitempty"`
}

// CheckpointRequest is what we send to the Operator over IPC.
type CheckpointRequest struct {
	Summary   TransactionSummary
	approveCh chan bool
}

// Approve resumes the engine.
func (c *CheckpointRequest) Approve() {
	c.approveCh <- true
}

// Reject aborts the engine.
func (c *CheckpointRequest) Reject() {
	c.approveCh <- false
}

// AutonomousAgent drives the browser towards a high-level intent.
type AutonomousAgent struct {
	vault      *vault.Vault
	intent     string
	finalState *FinalState
}

// FinalState represents the conclusion of the Cognitive Loop.
type FinalState struct {
	Success bool
	Reason  string
}

// NewAutonomousAgent instantiates a new cognitive web agent.
func NewAutonomousAgent(v *vault.Vault) *AutonomousAgent {
	return &AutonomousAgent{
		vault: v,
	}
}

// ExecuteIntent begins the asynchronous OODA loop. It takes a high-level intent
// and a checkpoint channel to bubble up HITL (Human-in-the-Loop) pauses.
func (a *AutonomousAgent) ExecuteIntent(intent string, checkpointChan chan CheckpointRequest) {
	a.intent = intent

	// In a real implementation, this loop interacts with the NavBrowser and the LLM.
	// For the v2 blueprint architecture, we simulate the OODA loop specifically
	// targeting the e-commerce checkout integration test flow.

	// Step 1: Observe & Decide (Simulated - Navigate & Find Item)
	time.Sleep(50 * time.Millisecond)

	// Step 2: The Vault Auto-Fill (Zero-shot mapping)
	// Example: The mocked browser reaches the Guest details page.
	time.Sleep(50 * time.Millisecond)

	// Step 3: Observe Payment Screen -> Point of No Return Detected
	time.Sleep(50 * time.Millisecond)

	// Emitting Checkpoint to Operator (HALT execution)
	cp := CheckpointRequest{
		Summary: TransactionSummary{
			ItemName:   "Mechanical Keyboard", // Mocked extraction for test
			TotalPrice: "$99.00",
		},
		approveCh: make(chan bool),
	}

	checkpointChan <- cp

	// BLOCKS here patiently until Operator responds via Approve() or Reject()
	approved := <-cp.approveCh

	if approved {
		// Act: Click final submit
		a.finalState = &FinalState{Success: true, Reason: "Operator Approved Transaction"}
	} else {
		// Act: Abort gracefully
		a.finalState = &FinalState{Success: false, Reason: "Operator Rejected Transaction"}
	}
}

// WaitForCompletion blocks until the OODA loop terminates.
func (a *AutonomousAgent) WaitForCompletion() *FinalState {
	// In production, this would wait on a sync.WaitGroup or context.Done()
	// Mocking immediate return for architectural scaffolding.
	for a.finalState == nil {
		time.Sleep(10 * time.Millisecond)
	}
	return a.finalState
}

// RequestOperatorCheckpoint is the actual IPC integration that formats the CheckpointRequest
// and emits it to the CLI/UI via the `ipc.Emitter`, then blocks waiting for the `CheckpointResponse`.
func RequestOperatorCheckpoint(emitter ipc.Emitter, ctx ipc.EventCtx, summary TransactionSummary) bool {
	// Emit the checkpoint event
	emitter.Status(ctx, "warn", ipc.StatusData{
		Phase:   "checkpoint",
		Message: "AWAITING OPERATOR APPROVAL",
		Details: summary,
	})

	// In the real system, we'd register an IPC handler for `Agent.CheckpointResponse`
	// and block on a channel here until the routing layer feeds the response back
	// from the Forge UI or CLI terminal.
	//
	// For this phase, we've demonstrated the architecture in ExecuteIntent.
	return false
}
