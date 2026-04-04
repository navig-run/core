import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

from navig.tools.interfaces import (
    ExecutionEvent,
    ExecutionRequest,
    StreamError,
    StreamFinal,
    ToolSpec,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Executes a single ToolSpec against an ExecutionRequest in a strict pipeline:
    validate -> execute -> stream -> finalize.
    """

    def __init__(self, tool_spec: ToolSpec, handler: Callable[..., Any]):
        self.spec = tool_spec
        self.handler = handler

    async def execute(
        self,
        request: ExecutionRequest,
    ) -> AsyncGenerator[ExecutionEvent, None]:
        """
        Main entry point for tool execution. Yields ExecutionEvent objects
        and guarantees a StreamFinal or StreamError as the terminal event.
        """
        # 1. Dispatch Check
        if request.is_cancelled:
            yield StreamError("Execution cancelled before start", code="cancelled")
            return

        # 2. Validate
        try:
            self._validate(request)
        except Exception as e:
            logger.warning("Tool %s validation failed: %s", self.spec.id, e)
            yield StreamError(str(e), code="validation_error")
            return

        # 3. Setup streaming callback
        async def on_event(event: ExecutionEvent) -> None:
            # this callback allows the inner tool to stream explicitly if it supports it
            pass  # The loop below handles the direct yields instead of callbacks, but we can pass it if needed.

        # 4. Execute with timeout and cancellation
        try:
            # We wrap the invocation to enforce timeouts
            coro = self._invoke_handler(request)

            # Allow graceful cancellation
            if request.cancellation_token:
                cancel_task = asyncio.create_task(request.cancellation_token.wait())
                exec_task = asyncio.create_task(coro)

                done, pending = await asyncio.wait(
                    [cancel_task, exec_task],
                    timeout=request.timeout_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if cancel_task in done:
                    exec_task.cancel()
                    yield StreamError("Execution cancelled", code="cancelled")
                    return
                elif not done:
                    # Timeout
                    exec_task.cancel()
                    cancel_task.cancel()
                    yield StreamError(
                        f"Execution timed out after {request.timeout_s}s",
                        code="timeout",
                    )
                    return
                else:
                    cancel_task.cancel()
                    # It finished successfully
                    output = exec_task.result()
            else:
                # No cancel token, just timeout
                output = await asyncio.wait_for(coro, timeout=request.timeout_s)

            # 5. Finalize
            yield StreamFinal(output=output)

        except asyncio.TimeoutError:
            yield StreamError(f"Execution timed out after {request.timeout_s}s", code="timeout")
        except asyncio.CancelledError:
            yield StreamError("Execution cancelled", code="cancelled")
        except Exception as e:
            logger.exception("Error executing tool %s", self.spec.id)
            yield StreamError(str(e), code="execution_error")

    def _validate(self, request: ExecutionRequest) -> None:
        """Enforces schema, permissions, and gates."""
        if self.spec.owner_only and not request.context.owner_only:
            raise PermissionError(f"Tool {self.spec.id} requires owner privileges.")

        # In a full implementation, validate request.args against self.spec.parameters
        if not self.spec.validate_args(request.args):
            raise ValueError(f"Invalid arguments for tool {self.spec.id}")

    async def _invoke_handler(self, request: ExecutionRequest) -> Any:
        """Safely invokes the underlying python function."""
        # Provide context if the handler asks for it, otherwise just pass args
        import inspect

        sig = inspect.signature(self.handler)

        kwargs = dict(request.args)
        if "context" in sig.parameters:
            kwargs["context"] = request.context

        if inspect.iscoroutinefunction(self.handler):
            return await self.handler(**kwargs)
        else:
            # Run sync functions in threadpool
            return await asyncio.to_thread(self.handler, **kwargs)
