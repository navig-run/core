"""
Unit tests for the NAVIG Agent Module.

Tests the autonomous agent system components:
- AgentConfig
- Component lifecycle
- NervousSystem (event bus)
- Heart (orchestrator)
- Eyes (monitoring)
- Ears (input)
- Hands (execution)
- Brain (intelligence)
- Soul (personality)
"""

import asyncio

import pytest

from navig.agent.brain import Brain, Decision, Plan, Thought, ThoughtType
from navig.agent.component import Component, ComponentState, HealthStatus

# Import agent components
from navig.agent.config import (
    AgentConfig,
    BrainConfig,
    EarsConfig,
    EyesConfig,
    HandsConfig,
    HeartConfig,
    PersonalityConfig,
)
from navig.agent.ears import Ears, InputMessage
from navig.agent.eyes import Alert, Eyes, SystemMetrics
from navig.agent.hands import CommandResult, CommandStatus, Hands
from navig.agent.heart import Heart
from navig.agent.nervous_system import Event, EventPriority, EventType, NervousSystem
from navig.agent.soul import BUILTIN_PROFILES, Mood, Soul

# ============================================================================
# CONFIG TESTS
# ============================================================================


class TestAgentConfig:
    """Test AgentConfig loading and saving."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AgentConfig()

        assert config.enabled is True
        assert config.mode == "autonomous"
        assert config.personality.profile == "friendly"
        assert config.brain.model == "openrouter:google/gemini-2.5-flash"
        assert config.eyes.monitoring_interval == 60
        assert config.hands.safe_mode is True

    def test_config_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "agent": {
                "enabled": False,
                "mode": "supervised",
                "personality": {
                    "profile": "professional",
                    "name": "TestBot",
                },
                "brain": {
                    "model": "openai:gpt-4",
                    "temperature": 0.5,
                },
            }
        }

        config = AgentConfig.from_dict(data)

        assert config.enabled is False
        assert config.mode == "supervised"
        assert config.personality.profile == "professional"
        assert config.personality.name == "TestBot"
        assert config.brain.model == "openai:gpt-4"
        assert config.brain.temperature == 0.5

    def test_config_save_load(self, tmp_path):
        """Test saving and loading config."""
        config_path = tmp_path / "config.yaml"

        config = AgentConfig()
        config.personality.profile = "witty"
        config.mode = "observe-only"

        config.save(config_path)

        loaded = AgentConfig.load(config_path)

        assert loaded.personality.profile == "witty"
        assert loaded.mode == "observe-only"

    def test_config_to_dict(self):
        """Test config serialization."""
        config = AgentConfig()
        data = config.to_dict()

        assert "enabled" in data
        assert "mode" in data
        assert "brain" in data
        assert "personality" in data


class TestPersonalityConfig:
    """Test PersonalityConfig."""

    def test_default_personality(self):
        """Test default personality values."""
        config = PersonalityConfig()

        assert config.name == "NAVIG"
        assert config.profile == "friendly"
        assert config.proactive is True
        assert config.emoji_enabled is True

    def test_personality_from_dict(self):
        """Test creating personality from dict."""
        data = {
            "name": "TestBot",
            "profile": "professional",
            "proactive": False,
            "emoji_enabled": False,
        }

        config = PersonalityConfig.from_dict(data)

        assert config.name == "TestBot"
        assert config.profile == "professional"
        assert config.proactive is False
        assert config.emoji_enabled is False


# ============================================================================
# COMPONENT TESTS
# ============================================================================


class TestComponent:
    """Test base Component class."""

    class MockComponent(Component):
        """Mock component for testing."""

        def __init__(self, name: str, nervous_system=None):
            super().__init__(name, nervous_system)
            self.start_called = False
            self.stop_called = False

        async def _on_start(self):
            self.start_called = True

        async def _on_stop(self):
            self.stop_called = True

        async def _on_health_check(self):
            return {"mock": True}

    @pytest.mark.asyncio
    async def test_component_lifecycle(self):
        """Test component start/stop lifecycle."""
        comp = self.MockComponent("test")

        assert comp.state == ComponentState.CREATED
        assert not comp.is_running

        await comp.start()

        assert comp.state == ComponentState.RUNNING
        assert comp.is_running
        assert comp.start_called
        # Uptime should be non-negative (may be 0 due to timing)
        assert comp.uptime_seconds >= 0

        await comp.stop()

        assert comp.state == ComponentState.STOPPED
        assert not comp.is_running
        assert comp.stop_called

    @pytest.mark.asyncio
    async def test_component_health_check(self):
        """Test component health check."""
        comp = self.MockComponent("test")
        await comp.start()

        health = await comp.health_check()

        assert isinstance(health, HealthStatus)
        assert health.healthy is True
        assert health.state == ComponentState.RUNNING
        assert health.details["mock"] is True

    @pytest.mark.asyncio
    async def test_component_restart(self):
        """Test component restart."""
        comp = self.MockComponent("test")
        await comp.start()

        await comp.restart()

        assert comp.state == ComponentState.RUNNING
        assert comp._restart_count == 1

    def test_component_status(self):
        """Test get_status method."""
        comp = self.MockComponent("test")
        status = comp.get_status()

        assert "name" in status
        assert "state" in status
        assert "running" in status
        assert status["name"] == "test"


# ============================================================================
# NERVOUS SYSTEM TESTS
# ============================================================================


class TestNervousSystem:
    """Test event bus functionality."""

    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self):
        """Test subscribing to and emitting events."""
        ns = NervousSystem()
        received_events = []

        def handler(event):
            received_events.append(event)

        ns.subscribe(EventType.SYSTEM_INFO, handler)

        event = await ns.emit(EventType.SYSTEM_INFO, source="test", data={"msg": "hello"})

        # Allow async handlers to complete
        await asyncio.sleep(0.01)

        assert len(received_events) == 1
        assert received_events[0].type == EventType.SYSTEM_INFO
        assert received_events[0].source == "test"
        assert received_events[0].data["msg"] == "hello"

    @pytest.mark.asyncio
    async def test_subscribe_all(self):
        """Test subscribing to all events."""
        ns = NervousSystem()
        received_events = []

        def handler(event):
            received_events.append(event)

        ns.subscribe_all(handler)

        await ns.emit(EventType.SYSTEM_INFO, source="test1")
        await ns.emit(EventType.HEARTBEAT, source="test2")

        await asyncio.sleep(0.01)

        assert len(received_events) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        """Test unsubscribing from events."""
        ns = NervousSystem()
        received_events = []

        def handler(event):
            received_events.append(event)

        ns.subscribe(EventType.SYSTEM_INFO, handler)
        await ns.emit(EventType.SYSTEM_INFO, source="test1")

        ns.unsubscribe(EventType.SYSTEM_INFO, handler)
        await ns.emit(EventType.SYSTEM_INFO, source="test2")

        await asyncio.sleep(0.01)

        assert len(received_events) == 1

    @pytest.mark.asyncio
    async def test_event_history(self):
        """Test event history tracking."""
        ns = NervousSystem()

        await ns.emit(EventType.SYSTEM_INFO, source="test1")
        await ns.emit(EventType.HEARTBEAT, source="test2")

        history = ns.get_history()

        assert len(history) == 2
        assert history[0].type == EventType.SYSTEM_INFO
        assert history[1].type == EventType.HEARTBEAT

    @pytest.mark.asyncio
    async def test_event_priority(self):
        """Test event priority levels."""
        event = Event(
            type=EventType.SYSTEM_ERROR,
            source="test",
            priority=EventPriority.CRITICAL,
        )

        assert event.priority == EventPriority.CRITICAL

    def test_nervous_system_stats(self):
        """Test stats collection."""
        ns = NervousSystem()

        def handler(e):
            pass

        ns.subscribe(EventType.HEARTBEAT, handler)
        ns.subscribe(EventType.SYSTEM_INFO, handler)

        stats = ns.get_stats()

        assert "total_events" in stats
        assert "handler_count" in stats
        assert stats["handler_count"] == 2


# ============================================================================
# HEART TESTS
# ============================================================================


class TestHeart:
    """Test Heart orchestrator."""

    @pytest.mark.asyncio
    async def test_heart_lifecycle(self):
        """Test heart start/stop."""
        ns = NervousSystem()
        config = HeartConfig()
        heart = Heart(config, ns)

        await heart.start()

        assert heart.is_running

        await heart.stop()

        assert not heart.is_running

    @pytest.mark.asyncio
    async def test_component_registration(self):
        """Test registering components."""
        ns = NervousSystem()
        config = HeartConfig()
        heart = Heart(config, ns)

        eyes_config = EyesConfig()
        eyes = Eyes(eyes_config, ns)

        heart.register_component("eyes", eyes)

        component = heart.get_component("eyes")
        assert component is eyes

    def test_heart_status(self):
        """Test heart status method."""
        ns = NervousSystem()
        config = HeartConfig()
        heart = Heart(config, ns)

        status = heart.get_status()

        assert "heart" in status
        assert "agent" in status
        assert "components" in status


# ============================================================================
# EYES TESTS
# ============================================================================


class TestEyes:
    """Test Eyes monitoring component."""

    @pytest.mark.asyncio
    async def test_eyes_lifecycle(self):
        """Test eyes start/stop."""
        config = EyesConfig()
        eyes = Eyes(config)

        await eyes.start()
        assert eyes.is_running

        await eyes.stop()
        assert not eyes.is_running

    @pytest.mark.asyncio
    async def test_collect_metrics(self):
        """Test metrics collection."""
        config = EyesConfig()
        eyes = Eyes(config)

        metrics = await eyes.collect_metrics()

        assert isinstance(metrics, SystemMetrics)
        assert metrics.timestamp is not None
        # Values depend on psutil availability

    def test_system_info(self):
        """Test system info collection."""
        config = EyesConfig()
        eyes = Eyes(config)

        info = eyes.get_system_info()

        assert "platform" in info
        assert "hostname" in info
        assert "python_version" in info

    def test_alert_creation(self):
        """Test alert creation."""
        alert = Alert(
            level="warning",
            category="cpu",
            message="High CPU usage",
            value=95.0,
            threshold=80.0,
        )

        data = alert.to_dict()

        assert data["level"] == "warning"
        assert data["category"] == "cpu"
        assert data["value"] == 95.0


# ============================================================================
# EARS TESTS
# ============================================================================


class TestEars:
    """Test Ears input listener component."""

    @pytest.mark.asyncio
    async def test_ears_lifecycle(self):
        """Test ears start/stop."""
        config = EarsConfig()
        config.api_enabled = False  # Disable API to avoid port conflicts
        ears = Ears(config)

        await ears.start()
        assert ears.is_running

        await ears.stop()
        assert not ears.is_running

    def test_input_message(self):
        """Test InputMessage creation."""
        msg = InputMessage(
            source="telegram",
            content="Hello world",
            user_id="12345",
            channel_id="67890",
        )

        data = msg.to_dict()

        assert data["source"] == "telegram"
        assert data["content"] == "Hello world"
        assert data["user_id"] == "12345"
        assert data["timestamp"] is not None


# ============================================================================
# HANDS TESTS
# ============================================================================


class TestHands:
    """Test Hands execution component."""

    @pytest.mark.asyncio
    async def test_hands_lifecycle(self):
        """Test hands start/stop."""
        config = HandsConfig()
        hands = Hands(config)

        await hands.start()
        assert hands.is_running

        await hands.stop()
        assert not hands.is_running

    @pytest.mark.asyncio
    async def test_execute_simple_command(self):
        """Test executing a simple command."""
        config = HandsConfig(safe_mode=False)
        hands = Hands(config)
        await hands.start()

        # Use a cross-platform command
        import sys

        if sys.platform == "win32":
            result = await hands.execute("echo hello", force=True)
        else:
            result = await hands.execute("echo hello", force=True)

        assert result.status == CommandStatus.COMPLETED
        assert "hello" in result.stdout.lower() or result.exit_code == 0

        await hands.stop()

    def test_dangerous_command_detection(self):
        """Test detection of dangerous commands."""
        config = HandsConfig()
        hands = Hands(config)

        assert hands._is_dangerous("rm -rf /")
        assert hands._is_dangerous("drop database test")
        assert hands._is_dangerous("shutdown now")
        assert not hands._is_dangerous("ls -la")
        assert not hands._is_dangerous("echo hello")

    def test_sudo_detection(self):
        """Test detection of sudo commands."""
        config = HandsConfig()
        hands = Hands(config)

        assert hands._is_sudo_command("sudo apt update")
        assert not hands._is_sudo_command("apt update")

    def test_command_result(self):
        """Test CommandResult creation."""
        result = CommandResult(
            command="echo test",
            status=CommandStatus.COMPLETED,
            exit_code=0,
            stdout="test\n",
            stderr="",
            duration_seconds=0.05,
        )

        assert result.success is True

        data = result.to_dict()
        assert data["success"] is True
        assert data["exit_code"] == 0


# ============================================================================
# BRAIN TESTS
# ============================================================================


class TestBrain:
    """Test Brain intelligence component."""

    @pytest.mark.asyncio
    async def test_brain_lifecycle(self):
        """Test brain start/stop."""
        config = BrainConfig()
        brain = Brain(config)

        await brain.start()
        assert brain.is_running

        await brain.stop()
        assert not brain.is_running

    def test_thought_creation(self):
        """Test Thought creation."""
        thought = Thought(
            type=ThoughtType.OBSERVATION,
            content="System load is high",
            confidence=0.9,
        )

        data = thought.to_dict()

        assert data["type"] == "OBSERVATION"
        assert data["content"] == "System load is high"
        assert data["confidence"] == 0.9

    def test_plan_creation(self):
        """Test Plan creation."""
        plan = Plan(
            goal="Reduce disk usage",
            steps=["Find large files", "Delete old logs", "Clear cache"],
            reasoning="Disk is over 85% full",
            priority=8,
        )

        data = plan.to_dict()

        assert data["goal"] == "Reduce disk usage"
        assert len(data["steps"]) == 3
        assert data["priority"] == 8

    def test_decision_creation(self):
        """Test Decision creation."""
        decision = Decision(
            question="Should I restart the service?",
            choice="yes",
            alternatives=["no", "wait"],
            reasoning="Service is unresponsive",
            confidence=0.85,
        )

        data = decision.to_dict()

        assert data["choice"] == "yes"
        assert data["confidence"] == 0.85


# ============================================================================
# SOUL TESTS
# ============================================================================


class TestSoul:
    """Test Soul personality component."""

    @pytest.mark.asyncio
    async def test_soul_lifecycle(self):
        """Test soul start/stop."""
        config = PersonalityConfig()
        soul = Soul(config)

        await soul.start()
        assert soul.is_running

        await soul.stop()
        assert not soul.is_running

    def test_builtin_profiles(self):
        """Test built-in personality profiles exist."""
        assert "friendly" in BUILTIN_PROFILES
        assert "professional" in BUILTIN_PROFILES
        assert "witty" in BUILTIN_PROFILES
        assert "paranoid" in BUILTIN_PROFILES
        assert "minimal" in BUILTIN_PROFILES

    def test_personality_profile(self):
        """Test PersonalityProfile."""
        profile = BUILTIN_PROFILES["friendly"]

        assert profile.name == "NAVIG"
        assert profile.emoji_enabled is True
        assert profile.proactive is True
        assert profile.formal is False

    def test_format_response(self):
        """Test response formatting."""
        config = PersonalityConfig(profile="friendly")
        soul = Soul(config)

        formatted = soul.format_response("Task completed", response_type="success")

        assert "completed" in formatted.lower() or "✅" in formatted

    def test_get_greeting(self):
        """Test greeting message."""
        config = PersonalityConfig(profile="friendly")
        soul = Soul(config)

        greeting = soul.get_greeting()

        assert len(greeting) > 0

    def test_switch_profile(self):
        """Test switching personality profiles."""
        config = PersonalityConfig(profile="friendly")
        soul = Soul(config)

        result = soul.switch_profile("professional")

        assert result is True
        assert soul._profile.formal is True

    @pytest.mark.asyncio
    async def test_mood_setting(self):
        """Test setting mood."""
        config = PersonalityConfig()
        soul = Soul(config)
        await soul.start()

        await soul.set_mood(Mood.ALERT, "Critical alert")

        mood, reason = soul.get_mood()

        assert mood == Mood.ALERT
        assert reason == "Critical alert"

        await soul.stop()

    def test_list_profiles(self):
        """Test listing available profiles."""
        config = PersonalityConfig()
        soul = Soul(config)

        profiles = soul.list_profiles()

        assert "friendly" in profiles
        assert "professional" in profiles


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestAgentIntegration:
    """Integration tests for agent components."""

    @pytest.mark.asyncio
    async def test_heart_with_components(self):
        """Test heart orchestrating multiple components."""
        ns = NervousSystem()
        heart_config = HeartConfig()
        heart = Heart(heart_config, ns)

        eyes = Eyes(EyesConfig())
        hands = Hands(HandsConfig())

        heart.register_component("eyes", eyes)
        heart.register_component("hands", hands)

        await heart.start()

        # All components should be running
        assert eyes.is_running
        assert hands.is_running

        await heart.stop()

        # All components should be stopped
        assert not eyes.is_running
        assert not hands.is_running

    @pytest.mark.asyncio
    async def test_event_propagation(self):
        """Test events propagating through nervous system."""
        ns = NervousSystem()
        received_events = []

        async def handler(event):
            received_events.append(event)

        ns.subscribe(EventType.ALERT_TRIGGERED, handler)

        eyes = Eyes(EyesConfig(), ns)
        await eyes.start()

        # Emit an alert manually
        await ns.emit(
            EventType.ALERT_TRIGGERED,
            source="eyes",
            data={"alert": {"level": "warning", "message": "test"}},
        )

        await asyncio.sleep(0.01)

        assert len(received_events) == 1

        await eyes.stop()


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def agent_config():
    """Create a test agent config."""
    return AgentConfig()


@pytest.fixture
def nervous_system():
    """Create a test nervous system."""
    return NervousSystem()


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory."""
    config_dir = tmp_path / "agent"
    config_dir.mkdir()
    (config_dir / "personalities").mkdir()
    (config_dir / "logs").mkdir()
    (config_dir / "workspace").mkdir()
    return config_dir
