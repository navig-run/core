package ipc

import (
	"context"
	"encoding/json"

	"navig-core/host/internal/browser"
)

type TaskRouterContext interface {
	ExecuteTask(ctx context.Context, req browser.TaskRunRequest, emitter Emitter, allowFallback bool, cloneOnBusy bool) (*browser.TaskRunResponse, error)
}

func RegisterTaskHandlers(server *Server, taskRouter TaskRouterContext) {
	server.Handlers["Task.Run"] = func(params json.RawMessage) (interface{}, error) {
		var req browser.TaskRunRequest
		if err := json.Unmarshal(params, &req); err != nil {
			return nil, err
		}

		return taskRouter.ExecuteTask(context.Background(), req, server.Emitter, false, false)
	}
}
