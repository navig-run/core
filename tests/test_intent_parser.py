"""
Tests for NAVIG Bot Intent Parser

Tests natural language → command mapping accuracy.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch

# Import the modules we're testing
from navig.bot.intent_parser import IntentParser, IntentParseResult, ConfirmationHandler
from navig.bot.command_tools import COMMAND_TOOLS, get_command_string, COMMAND_HANDLER_MAP


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def intent_parser():
    """Create an IntentParser with AI disabled (pattern matching only)."""
    return IntentParser(config_manager=None, enable_ai=False)


@pytest.fixture
def confirmation_handler():
    """Create a ConfirmationHandler."""
    return ConfirmationHandler()


# ============================================================================
# Pattern Matching Tests
# ============================================================================

class TestPatternMatching:
    """Test regex pattern-based intent detection."""
    
    def test_docker_containers_variations(self, intent_parser):
        """Test various ways to ask for Docker containers."""
        queries = [
            "show me docker containers",
            "list all containers",
            "what docker containers are running",
            "display the containers",
            "get docker containers",
            "docker ps",
        ]
        
        for query in queries:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "docker_ps", f"Failed for: '{query}' - got {result.command}"
            assert result.confidence >= 0.7, f"Low confidence for: '{query}'"
    
    def test_disk_space_variations(self, intent_parser):
        """Test disk space queries."""
        queries = [
            "check disk space",
            "how much space is left",
            "show disk usage",
            "what's the disk status",
            "check storage",
        ]
        
        for query in queries:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "disk", f"Failed for: '{query}' - got {result.command}"
            assert result.confidence >= 0.7
    
    def test_memory_variations(self, intent_parser):
        """Test memory queries."""
        queries = [
            "check memory",
            "how much ram is used",
            "show memory usage",
            "check free memory",
        ]
        
        for query in queries:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "memory", f"Failed for: '{query}' - got {result.command}"
            assert result.confidence >= 0.7
    
    def test_switch_host(self, intent_parser):
        """Test host switching."""
        queries_and_hosts = [
            ("switch to production", "production"),
            ("use staging", "staging"),
            ("connect to dev-server", "dev-server"),
            ("select server prod01", "prod01"),
        ]
        
        for query, expected_host in queries_and_hosts:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "use_host", f"Failed for: '{query}'"
            assert result.args.get("host_name") == expected_host, f"Wrong host for: '{query}'"
    
    def test_list_hosts(self, intent_parser):
        """Test listing hosts."""
        queries = [
            "list all hosts",
            "show servers",
            "get machines",
            "list hosts",
        ]
        
        for query in queries:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "hosts", f"Failed for: '{query}'"
    
    def test_docker_logs(self, intent_parser):
        """Test Docker logs queries."""
        result = intent_parser._parse_with_patterns("logs from nginx")
        assert result.command == "docker_logs"
        assert result.args.get("container") == "nginx"
    
    def test_weather(self, intent_parser):
        """Test weather queries."""
        queries_and_locations = [
            ("weather in London", "London"),
            ("weather", ""),  # No location
        ]
        
        for query, expected_loc in queries_and_locations:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "weather", f"Failed for: '{query}'"
            if expected_loc:
                assert expected_loc.lower() in result.args.get("location", "").lower()
    
    def test_crypto_prices(self, intent_parser):
        """Test cryptocurrency price queries."""
        queries = [
            ("btc price", "BTC"),
            ("bitcoin", "BTC"),
            ("price of eth", "ETH"),
            ("ethereum price", "ETH"),
        ]
        
        for query, expected_symbol in queries:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "crypto", f"Failed for: '{query}'"
            assert result.args.get("symbol") == expected_symbol, f"Wrong symbol for: '{query}'"
    
    def test_currency_conversion(self, intent_parser):
        """Test currency conversion queries."""
        result = intent_parser._parse_with_patterns("convert 100 usd to eur")
        assert result.command == "convert"
        assert result.args.get("amount") == 100
        assert result.args.get("from_currency") == "USD"
        assert result.args.get("to_currency") == "EUR"
    
    def test_reminder_parsing(self, intent_parser):
        """Test reminder parsing."""
        result = intent_parser._parse_with_patterns("remind me in 30 minutes to check the logs")
        assert result.command == "remind"
        assert result.args.get("duration") == 30
        assert result.args.get("unit") == "minutes"
        assert "logs" in result.args.get("message", "").lower()
    
    def test_fun_commands(self, intent_parser):
        """Test fun/random commands."""
        assert intent_parser._parse_with_patterns("flip a coin").command == "flip"
        assert intent_parser._parse_with_patterns("roll d20").command == "roll"
        assert intent_parser._parse_with_patterns("tell me a joke").command == "joke"
    
    def test_ssl_check(self, intent_parser):
        """Test SSL certificate check."""
        result = intent_parser._parse_with_patterns("check ssl for example.com")
        assert result.command == "ssl"
        assert "example.com" in result.args.get("domain", "")
    
    def test_no_match(self, intent_parser):
        """Test that gibberish returns no command."""
        queries = [
            "asdfghjkl",
            "random gibberish here",
            "hello how are you today",
        ]
        
        for query in queries:
            result = intent_parser._parse_with_patterns(query)
            # Should have low confidence or no command
            assert result.confidence < 0.6 or result.command is None


# ============================================================================
# Command String Generation Tests
# ============================================================================

class TestCommandStringGeneration:
    """Test command string generation from parsed intents."""
    
    def test_simple_commands(self):
        """Test simple commands without arguments."""
        assert get_command_string("docker_ps", {}) == "/docker"
        assert get_command_string("hosts", {}) == "/hosts"
        assert get_command_string("status", {}) == "/status"
        assert get_command_string("flip", {}) == "/flip"
    
    def test_commands_with_args(self):
        """Test commands with arguments."""
        assert get_command_string("use_host", {"host_name": "production"}) == "/use production"
        assert "nginx" in get_command_string("docker_logs", {"container": "nginx"})
        assert "example.com" in get_command_string("ssl", {"domain": "example.com"})
    
    def test_currency_conversion(self):
        """Test currency conversion command string."""
        result = get_command_string("convert", {
            "amount": 100,
            "from_currency": "USD",
            "to_currency": "EUR"
        })
        assert "/convert" in result
        assert "100" in result
        assert "USD" in result
        assert "EUR" in result
    
    def test_reminder_command(self):
        """Test reminder command string."""
        result = get_command_string("remind", {
            "message": "check logs",
            "duration": 30,
            "unit": "minutes"
        })
        assert "/remind" in result
        assert "30" in result
        assert "m" in result  # Short unit
        assert "check logs" in result
    
    def test_unknown_command(self):
        """Test unknown command returns None."""
        assert get_command_string("nonexistent_command", {}) is None


# ============================================================================
# IntentParseResult Tests
# ============================================================================

class TestIntentParseResult:
    """Test IntentParseResult dataclass."""
    
    def test_is_command_true(self):
        """Test is_command when command is detected."""
        result = IntentParseResult(
            command="docker_ps",
            args={},
            confidence=0.9,
            raw_message="show docker containers"
        )
        assert result.is_command is True
    
    def test_is_command_false_no_command(self):
        """Test is_command when no command detected."""
        result = IntentParseResult(
            command=None,
            args={},
            confidence=0.0,
            raw_message="hello"
        )
        assert result.is_command is False
    
    def test_is_command_false_zero_confidence(self):
        """Test is_command when confidence is zero."""
        result = IntentParseResult(
            command="docker_ps",
            args={},
            confidence=0.0,
            raw_message="maybe docker?"
        )
        assert result.is_command is False
    
    def test_to_command_string(self):
        """Test conversion to command string."""
        result = IntentParseResult(
            command="use_host",
            args={"host_name": "production"},
            confidence=0.9,
            raw_message="switch to production"
        )
        assert result.to_command_string() == "/use production"
    
    def test_to_dict(self):
        """Test dictionary conversion."""
        result = IntentParseResult(
            command="disk",
            args={},
            confidence=0.85,
            raw_message="check disk space",
            method="pattern"
        )
        d = result.to_dict()
        assert d["command"] == "disk"
        assert d["confidence"] == 0.85
        assert d["method"] == "pattern"


# ============================================================================
# Confirmation Handler Tests
# ============================================================================

class TestConfirmationHandler:
    """Test confirmation flow for low-confidence intents."""
    
    def test_set_and_get_pending(self, confirmation_handler):
        """Test setting and getting pending commands."""
        result = IntentParseResult(
            command="docker_restart",
            args={"container": "nginx"},
            confidence=0.5,
            raw_message="restart the container"
        )
        
        confirmation_handler.set_pending(123, result)
        pending = confirmation_handler.get_pending(123)
        
        assert pending is not None
        assert pending.command == "docker_restart"
    
    def test_clear_pending(self, confirmation_handler):
        """Test clearing pending commands."""
        result = IntentParseResult(
            command="backup",
            args={"action": "create"},
            confidence=0.5,
            raw_message="create a backup"
        )
        
        confirmation_handler.set_pending(123, result)
        confirmation_handler.clear_pending(123)
        
        assert confirmation_handler.get_pending(123) is None
    
    def test_is_confirmation_yes(self, confirmation_handler):
        """Test confirmation detection for yes responses."""
        confirmations = ['yes', 'y', 'yeah', 'yep', 'sure', 'ok', 'okay', 'confirm', 'do it']
        
        for msg in confirmations:
            assert confirmation_handler.is_confirmation(msg), f"Failed for: '{msg}'"
            assert confirmation_handler.is_confirmed(msg), f"Should be confirmed: '{msg}'"
    
    def test_is_confirmation_no(self, confirmation_handler):
        """Test confirmation detection for no responses."""
        cancellations = ['no', 'n', 'nope', 'cancel', 'nevermind']
        
        for msg in cancellations:
            assert confirmation_handler.is_confirmation(msg), f"Failed for: '{msg}'"
            assert not confirmation_handler.is_confirmed(msg), f"Should not be confirmed: '{msg}'"
    
    def test_is_not_confirmation(self, confirmation_handler):
        """Test non-confirmation messages."""
        messages = ['hello', 'show docker containers', 'what is the status']
        
        for msg in messages:
            assert not confirmation_handler.is_confirmation(msg), f"Should not be confirmation: '{msg}'"


# ============================================================================
# Async Intent Parsing Tests
# ============================================================================

class TestAsyncIntentParsing:
    """Test async intent parsing."""
    
    @pytest.mark.asyncio
    async def test_parse_intent_slash_command_passthrough(self, intent_parser):
        """Test that slash commands are passed through."""
        result = await intent_parser.parse_intent("/docker")
        assert not result.is_command
        assert result.confidence == 0.0
    
    @pytest.mark.asyncio
    async def test_parse_intent_pattern_match(self, intent_parser):
        """Test pattern matching through async interface."""
        result = await intent_parser.parse_intent("show me docker containers")
        assert result.command == "docker_ps"
        assert result.confidence >= 0.7
    
    @pytest.mark.asyncio
    async def test_parse_intent_with_history(self, intent_parser):
        """Test parsing with conversation history (should not affect pattern matching)."""
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi! How can I help?"},
        ]
        
        result = await intent_parser.parse_intent("check disk space", history)
        assert result.command == "disk"


# ============================================================================
# Command Tools Registry Tests
# ============================================================================

class TestCommandToolsRegistry:
    """Test the command tools registry."""
    
    def test_all_commands_have_handlers(self):
        """Verify all commands in COMMAND_TOOLS have handlers."""
        for tool in COMMAND_TOOLS:
            func_name = tool['function']['name']
            assert func_name in COMMAND_HANDLER_MAP, f"Missing handler for: {func_name}"
    
    def test_tool_schema_valid(self):
        """Verify tool schemas are valid."""
        for tool in COMMAND_TOOLS:
            assert 'type' in tool
            assert tool['type'] == 'function'
            assert 'function' in tool
            func = tool['function']
            assert 'name' in func
            assert 'description' in func
            assert 'parameters' in func
    
    def test_command_count(self):
        """Verify we have a reasonable number of commands."""
        assert len(COMMAND_TOOLS) >= 40, "Expected at least 40 commands"


# ============================================================================
# Edge Cases and Regression Tests
# ============================================================================

class TestEdgeCases:
    """Test edge cases and potential issues."""
    
    def test_empty_message(self, intent_parser):
        """Test handling of empty messages."""
        result = intent_parser._parse_with_patterns("")
        assert not result.is_command
    
    def test_very_long_message(self, intent_parser):
        """Test handling of very long messages."""
        long_msg = "show docker containers " * 100
        result = intent_parser._parse_with_patterns(long_msg)
        # Should still match docker_ps at the beginning
        assert result.command == "docker_ps"
    
    def test_special_characters(self, intent_parser):
        """Test handling of special characters."""
        result = intent_parser._parse_with_patterns("check disk!!! @#$%")
        # Should still recognize disk command
        assert result.command == "disk"
    
    def test_mixed_case(self, intent_parser):
        """Test case insensitivity."""
        queries = [
            "SHOW DOCKER CONTAINERS",
            "Show Docker Containers",
            "sHoW dOcKeR cOnTaInErS",
        ]
        
        for query in queries:
            result = intent_parser._parse_with_patterns(query)
            assert result.command == "docker_ps", f"Failed for: '{query}'"
    
    def test_unicode_characters(self, intent_parser):
        """Test handling of unicode characters."""
        result = intent_parser._parse_with_patterns("show docker containers 🐳")
        assert result.command == "docker_ps"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
