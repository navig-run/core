package ipc

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"

	"navig-core/host/internal/browser"
)

type Server struct {
	in  *bufio.Scanner
	out io.Writer

	Emitter  Emitter
	Handlers map[string]func(params json.RawMessage) (interface{}, error)
}

func New(emitter Emitter) *Server {
	return &Server{
		in:       bufio.NewScanner(os.Stdin),
		out:      os.Stdout,
		Emitter:  emitter,
		Handlers: make(map[string]func(json.RawMessage) (interface{}, error)),
	}
}

func (s *Server) Respond(id string, result interface{}, err error) {
	resp := browser.AgentResponse{
		Id: id,
	}

	if err != nil {
		resp.Ok = false
		resp.Error = &browser.AgentError{
			Code:    -32000,
			Message: err.Error(),
		}
	} else {
		resp.Ok = true
		if result != nil {
			if b, err := json.Marshal(result); err == nil {
				resp.Result = b
			}
		}
	}

	// Envelope the response to match the "type":"response" structure as requested
	enveloped := struct {
		Type   string              `json:"type"`
		Id     string              `json:"id"`
		Ok     bool                `json:"ok"`
		Result json.RawMessage     `json:"result,omitempty"`
		Error  *browser.AgentError `json:"error,omitempty"`
	}{
		Type:   "response",
		Id:     resp.Id,
		Ok:     resp.Ok,
		Result: resp.Result,
		Error:  resp.Error,
	}

	s.Emitter.WriteJSONLine(enveloped)
}

func (s *Server) Run() error {
	for s.in.Scan() {
		line := s.in.Bytes()
		if len(line) == 0 {
			continue
		}

		var req browser.AgentRequest
		if err := json.Unmarshal(line, &req); err != nil {
			s.Respond("", nil, fmt.Errorf("invalid request: %v", err))
			continue
		}

		handler, ok := s.Handlers[req.Method]
		if !ok {
			s.Respond(req.Id, nil, fmt.Errorf("method not found: %s", req.Method))
			continue
		}

		res, err := handler(req.Params)
		s.Respond(req.Id, res, err)
	}

	if err := s.in.Err(); err != nil {
		return err
	}

	return nil
}
