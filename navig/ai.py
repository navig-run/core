"""
AI Assistant Integration (DEPRECATED)

Context-aware assistance from The Schema's analysis engines.
Supports multiple AI providers via the providers system.

.. deprecated::
    This module is deprecated. Use ``navig.llm_generate.llm_generate()`` as the
    canonical entry point for all LLM calls. The ``AIAssistant`` class will be
    removed in a future release. See docs/LLM_MODES.md for details.

Migration::

    # Old
    from navig.ai import AIAssistant
    assistant = AIAssistant(config_manager)
    response = assistant.ask(question, context)

    # New
    from navig.llm_generate import llm_generate
    response = llm_generate(messages=[{"role": "user", "content": question}], mode="big_tasks")
"""

import asyncio
import concurrent.futures as _cf
import json
from typing import Any, ClassVar, Dict, List, Optional

import requests

from navig import console_helper as ch
from navig.ai_context import get_ai_context_manager


class AIAssistant:
    """
    Multi-provider AI integration for context-aware server assistance.
    
    .. deprecated::
        Use ``navig.llm_generate.llm_generate()`` instead. This class is
        maintained for backward compatibility only.
    
    Supports OpenRouter, OpenAI, Anthropic, and more via the providers system.
    Falls back automatically if a provider fails.
    """

    def __init__(self, config_manager):
        self.config = config_manager
        self.api_url = "https://openrouter.ai/api/v1/chat/completions"
        self._fallback_manager = None

    # Class-level singleton for ConversationStore — opened once, reused across calls
    _conv_store: ClassVar[Optional[Any]] = None

    def _get_conv_store(self):
        """Return a cached ConversationStore singleton (opens DB once per process)."""
        if AIAssistant._conv_store is None:
            try:
                from pathlib import Path

                from navig.memory.conversation import ConversationStore
                AIAssistant._conv_store = ConversationStore(Path.home() / '.navig' / 'memory.db')
            except Exception:
                pass
        return AIAssistant._conv_store

    def _get_fallback_manager(self):
        """Lazy-load the fallback manager for multi-provider support."""
        if self._fallback_manager is None:
            try:
                from navig.providers import FallbackManager
                self._fallback_manager = FallbackManager()
            except ImportError:
                pass  # Providers not available, will use legacy mode
        return self._fallback_manager

    def ask(
        self,
        question: str,
        context: Dict[str, Any],
        model_override: Optional[str] = None,
        use_fallback: bool = True,
    ) -> str:
        """
        Ask AI a question with server context.
        
        Args:
            question: User's natural language question
            context: Server context (config, processes, logs, etc.)
            model_override: Override default model preference
            use_fallback: Use multi-provider fallback (default True)
        
        Returns:
            AI response text
        """
        # Get system prompt
        system_prompt = self.config.get_ai_system_prompt()

        # ── Memory enrichment: all 3 sources run in parallel ─────────────────
        # Each block is independent (different DBs/singletons) — no reason to sequence.
        # Total latency = max(T_kb, T_kg, T_episodic) instead of sum.
        _q = question[:200]  # consistent truncation for all three

        def _fetch_kb() -> str:
            try:
                from navig.memory.manager import get_memory_manager
                _mgr = get_memory_manager()
                _parts: list = []
                _mem_ctx = ""
                if hasattr(_mgr, 'get_context_for_query'):
                    _mem_ctx = _mgr.get_context_for_query(question, limit=5) or ""
                elif hasattr(_mgr, 'knowledge_base') and _mgr.knowledge_base:
                    _results = _mgr.knowledge_base.text_search(_q, limit=5)
                    if _results:
                        _mem_ctx = "\n".join(f"- {e.key}: {e.content[:150]}" for e in _results)
                if _mem_ctx:
                    _parts.append("\n\n## What I Know\n" + _mem_ctx)
                if hasattr(_mgr, 'get_user_context'):
                    _profile = _mgr.get_user_context() or ""
                    if _profile:
                        _parts.append("\n\n## User Profile\n" + _profile)
                return "".join(_parts)
            except Exception:
                return ""

        def _fetch_kg() -> str:
            try:
                from navig.memory.knowledge_graph import get_knowledge_graph
                _kg = get_knowledge_graph()
                _parts: list = []
                _kg_facts = _kg.search_facts(_q, limit=10)
                if _kg_facts:
                    _lines = [
                        f"- {f.subject} {f.predicate.replace('_', ' ')} {f.object}"
                        for f in _kg_facts[:8]
                    ]
                    _parts.append("\n\n## Known Facts (Graph)\n" + "\n".join(_lines))
                _routines = _kg.get_routines(enabled_only=True)
                if _routines:
                    _rlines = [f"- {r.name}: {r.description or r.schedule}" for r in _routines[:5]]
                    _parts.append("\n\n## Active Routines\n" + "\n".join(_rlines))
                return "".join(_parts)
            except Exception:
                return ""

        def _fetch_episodic() -> str:
            try:
                _cs = self._get_conv_store()
                if not _cs:
                    return ""
                _past = _cs.search_content(_q, limit=6)
                if not _past:
                    return ""
                _pairs: list = []
                _seen: set = set()
                for _m in _past:
                    if _m.role in ('user', 'assistant') and _m.session_key not in _seen:
                        _seen.add(_m.session_key)
                        _pairs.append(
                            f"[past session {_m.session_key[:8]}] {_m.role}: {_m.content[:200]}"
                        )
                        if len(_pairs) >= 3:
                            break
                return ("\n\n## Relevant Past Sessions\n" + "\n".join(_pairs)) if _pairs else ""
            except Exception:
                return ""

        with _cf.ThreadPoolExecutor(max_workers=3, thread_name_prefix="navig_mem") as _pool:
            _f_kb, _f_kg, _f_ep = (
                _pool.submit(_fetch_kb),
                _pool.submit(_fetch_kg),
                _pool.submit(_fetch_episodic),
            )
            system_prompt += _f_kb.result() + _f_kg.result() + _f_ep.result()

        # Build context string
        context_str = self._build_context_string(context)

        # Try the new provider system first if available
        fallback_mgr = self._get_fallback_manager() if use_fallback else None

        if fallback_mgr:
            try:
                return self._ask_with_providers(
                    fallback_mgr,
                    system_prompt,
                    context_str,
                    question,
                    model_override,
                )
            except Exception as e:
                ch.dim(f"Provider system error, falling back to legacy: {e}")

        # Fall back to legacy OpenRouter-only mode
        return self._ask_legacy(
            system_prompt,
            context_str,
            question,
            model_override,
        )

    def _ask_with_providers(
        self,
        fallback_mgr,
        system_prompt: str,
        context_str: str,
        question: str,
        model_override: Optional[str] = None,
    ) -> str:
        """Ask using the multi-provider fallback system."""
        from navig.providers import CompletionRequest, Message

        # Build messages
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"{context_str}\n\nUSER QUESTION: {question}"),
        ]

        # Determine model
        if model_override:
            model = model_override
        else:
            models = self.config.global_config.get('ai_model_preference', [])
            model = models[0] if models else "gpt-4o-mini"

        # Build fallback models
        fallback_models = []
        if not model_override:
            all_models = self.config.global_config.get('ai_model_preference', [
                'openrouter:deepseek/deepseek-coder-33b-instruct',
                'openai:gpt-4o-mini',
                'anthropic:claude-3-haiku-20240307',
            ])
            fallback_models = all_models[1:] if len(all_models) > 1 else []

        request = CompletionRequest(
            messages=messages,
            model=model,
            temperature=0.7,
            max_tokens=4096,
        )

        # Run with fallback (async)
        async def run():
            result = await fallback_mgr.run_with_fallback(
                request,
                fallback_models=fallback_models,
            )
            return result.response.content

        return asyncio.run(run())

    def _ask_legacy(
        self,
        system_prompt: str,
        context_str: str,
        question: str,
        model_override: Optional[str] = None,
    ) -> str:
        """Ask using legacy OpenRouter-only mode."""
        api_key = self.config.global_config.get('openrouter_api_key')
        if not api_key:
            raise ValueError(
                "OpenRouter API key not configured. "
                "Set it in ~/.navig/config.yaml or use 'navig config set openrouter_api_key <key>'"
            )

        # Determine model to use
        if model_override:
            models = [model_override]
        else:
            models = self.config.global_config.get('ai_model_preference', [
                'deepseek/deepseek-coder-33b-instruct',
                'google/gemini-flash-1.5',
                'qwen/qwen-2.5-72b-instruct',
                'meta-llama/llama-3.3-70b-instruct',
            ])

        # Try each model in fallback chain
        for model in models:
            try:
                response = self._call_openrouter(
                    api_key=api_key,
                    model=model,
                    system_prompt=system_prompt,
                    context=context_str,
                    question=question,
                )
                return response
            except Exception as e:
                ch.dim(f"Model {model} failed: {e}")
                continue

        raise RuntimeError("All AI models failed to respond")

    def _build_context_string(self, context: Dict[str, Any]) -> str:
        """Build context string for AI from gathered information."""
        lines = ["CONTEXT:"]

        if 'server' in context:
            server = context['server']
            lines.append(f"Server: {server.get('name')} ({server.get('host')})")
            lines.append(f"User: {server.get('user')}")

            if 'paths' in server:
                paths = server['paths']
                if paths.get('web_root'):
                    lines.append(f"Web Root: {paths['web_root']}")

        if 'directory' in context:
            lines.append(f"Current Directory: {context['directory']}")

        if 'processes' in context:
            lines.append("\nRunning Services:")
            for proc in context['processes']:
                lines.append(f"- {proc}")

        if 'logs' in context:
            lines.append("\nRecent Logs:")
            lines.append(context['logs'])

        if 'disk' in context:
            lines.append(f"\nDisk Usage: {context['disk']}")

        # Add recent error context for AI awareness
        try:
            ai_context_mgr = get_ai_context_manager()
            error_summary = ai_context_mgr.get_error_summary(hours=24)

            if error_summary['total_errors'] > 0:
                lines.append(f"\nRecent Errors (Last 24h): {error_summary['total_errors']}")

                if error_summary['categories']:
                    lines.append("Error Categories:")
                    for cat, count in sorted(error_summary['categories'].items(), key=lambda x: x[1], reverse=True):
                        lines.append(f"  - {cat}: {count}")

                if error_summary['common_errors']:
                    lines.append("\nMost Common Issues:")
                    for i, err in enumerate(error_summary['common_errors'][:3], 1):
                        lines.append(f"  {i}. [{err['category']}] {err['example'][:80]}...")
        except Exception:
            pass  # Don't fail if error context unavailable

        return "\n".join(lines)

    def _call_openrouter(
        self,
        api_key: str,
        model: str,
        system_prompt: str,
        context: str,
        question: str,
    ) -> str:
        """Call OpenRouter API."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{context}\n\nUSER QUESTION: {question}"},
            ],
        }

        response = requests.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=30,
        )

        response.raise_for_status()
        data = response.json()

        return data['choices'][0]['message']['content']

    def analyze_error(
        self,
        command: str,
        error_message: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Analyze error and suggest solutions using AI.

        Args:
            command: Command that failed
            error_message: Error message
            context: Execution context

        Returns:
            AI analysis and suggestions
        """
        prompt = f"""
        A command failed with the following error:

        Command: {command}
        Error: {error_message}

        Please analyze this error and provide:
        1. Root cause explanation
        2. Step-by-step solution
        3. Prevention tips for the future

        Be concise and actionable.
        """

        try:
            return self.ask(prompt, context)
        except Exception as e:
            return f"AI analysis unavailable: {e}"

    def suggest_optimization(
        self,
        workflow_pattern: Dict[str, Any],
        context: Dict[str, Any]
    ) -> str:
        """
        Suggest workflow optimizations based on detected patterns.

        Args:
            workflow_pattern: Detected workflow pattern
            context: Server context

        Returns:
            Optimization suggestions
        """
        prompt = f"""
        Detected workflow pattern:
        {json.dumps(workflow_pattern, indent=2)}

        Suggest optimizations to improve efficiency and reduce repetitive tasks.
        """

        try:
            return self.ask(prompt, context)
        except Exception as e:
            return f"AI suggestions unavailable: {e}"

    def generate_context_summary(
        self,
        full_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate enhanced context summary for AI copilot.

        Args:
            full_context: Complete context data

        Returns:
            Enhanced context dictionary
        """
        # This is handled by the ContextGenerator module
        # This method is kept for backward compatibility
        return full_context


def ask_ai_with_context(
    prompt: str,
    system_prompt: str = "",
    history: List[Dict[str, str]] = None,
    model: Optional[str] = None
) -> str:
    """
    Simple function to ask AI with context - used by gateway server.
    
    Args:
        prompt: User message/question
        system_prompt: System instructions
        history: Previous conversation messages
        model: Model to use (optional)
    
    Returns:
        AI response text
    """
    from navig.config import get_config_manager

    config_mgr = get_config_manager()
    ai_key = config_mgr.global_config.get('openrouter_api_key')

    if not ai_key:
        return "Error: OpenRouter API key not configured. Set it in ~/.navig/config.yaml"

    # Build messages
    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Add history
    if history:
        messages.extend(history)

    # Add current prompt
    messages.append({"role": "user", "content": prompt})

    # Determine model
    if not model:
        models = config_mgr.global_config.get('ai_model_preference', [])
        model = models[0] if models else "google/gemini-2.5-flash"

    # Call OpenRouter API
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {ai_key}",
                "HTTP-Referer": "https://github.com/navig-run/core",
                "X-Title": "NAVIG Autonomous Agent",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4096,
            },
            timeout=120
        )
        response.raise_for_status()

        data = response.json()
        return data['choices'][0]['message']['content']

    except requests.exceptions.RequestException as e:
        return f"Error calling AI API: {e}"
    except (KeyError, IndexError) as e:
        return f"Error parsing AI response: {e}"
