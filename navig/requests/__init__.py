"""
navig.requests — the unified "navig asks the user for a decision" primitive.

A single `UserRequest` shape powers three kinds of ask surfaced in the deck:

  approval  — yes/no on a command (produced by navig.approval.ApprovalManager)
  question  — pick one/many options, or write your own (produced here)
  route     — confirm where an inbox document should go (produced by the
              inbox deck route)

`RequestRegistry` is the producer/consumer for the question/route kinds. It
mirrors ApprovalManager's asyncio-future pattern: an agent can `await ask(...)`
and block until the deck answers, or `create(...)` a non-blocking request with
an `on_answer` callback that runs when the user responds.
"""

from navig.requests.registry import RequestRegistry, UserRequest
from navig.requests.autodispatch import should_auto_dispatch

__all__ = ["RequestRegistry", "UserRequest", "should_auto_dispatch"]
