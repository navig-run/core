"""Retry Logic and Auto-Recovery for Network Operations

Implements exponential backoff, timeout handling, and graceful degradation.
"""

import random
import time
from functools import wraps
from typing import Any, Callable, Optional

from navig import console_helper as ch
from navig.ai_context import log_error


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(self, max_retries: int = 3, base_delay: float = 1.0,
                 max_delay: float = 30.0, exponential_base: float = 2.0,
                 jitter: bool = True, timeout: Optional[float] = None):
        """Initialize retry configuration.
        
        Args:
            max_retries: Maximum number of retry attempts (0 = no retries)
            base_delay: Initial delay between retries in seconds
            max_delay: Maximum delay between retries (caps exponential growth)
            exponential_base: Base for exponential backoff (2.0 = double each time)
            jitter: Add random jitter to prevent thundering herd
            timeout: Overall timeout for all retries (None = no timeout)
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.timeout = timeout

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number.
        
        Args:
            attempt: Retry attempt number (0-indexed)
            
        Returns:
            Delay in seconds
        """
        # Exponential backoff: base_delay * (exponential_base ^ attempt)
        delay = self.base_delay * (self.exponential_base ** attempt)

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        # Add jitter (random 0-25% variation) to prevent thundering herd
        if self.jitter:
            jitter_amount = delay * 0.25 * random.random()
            delay += jitter_amount

        return delay


class RetryableOperation:
    """Wrapper for operations that can be retried."""

    def __init__(self, operation: Callable, config: RetryConfig,
                 error_category: str, command_name: str):
        """Initialize retryable operation.
        
        Args:
            operation: Function to execute (should raise exception on failure)
            config: Retry configuration
            error_category: Error category for logging (tunnel, database, file, etc.)
            command_name: Command name for error logging
        """
        self.operation = operation
        self.config = config
        self.error_category = error_category
        self.command_name = command_name
        self.start_time = None

    def execute(self, *args, **kwargs) -> Any:
        """Execute operation with retry logic.
        
        Returns:
            Operation result if successful
            
        Raises:
            Last exception if all retries exhausted
        """
        self.start_time = time.time()
        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            # Check overall timeout
            if self.config.timeout:
                elapsed = time.time() - self.start_time
                if elapsed >= self.config.timeout:
                    error_msg = f"Operation timed out after {elapsed:.1f}s"
                    log_error(
                        self.error_category,
                        self.command_name,
                        error_msg,
                        {'timeout': self.config.timeout, 'elapsed': elapsed}
                    )
                    raise TimeoutError(error_msg)

            try:
                # Attempt operation
                if attempt > 0:
                    ch.dim(f"Retry attempt {attempt}/{self.config.max_retries}...")

                result = self.operation(*args, **kwargs)

                # Success
                if attempt > 0:
                    ch.success(f"Operation succeeded after {attempt} retry(s)")

                return result

            except Exception as e:
                last_exception = e

                # If this was the last attempt, fail
                if attempt >= self.config.max_retries:
                    # Log final failure
                    log_error(
                        self.error_category,
                        self.command_name,
                        str(e),
                        {
                            'attempts': attempt + 1,
                            'total_time': time.time() - self.start_time
                        }
                    )
                    raise

                # Calculate delay for next attempt
                delay = self.config.get_delay(attempt)

                # Log retry info
                ch.warning(f"Operation failed: {str(e)[:80]}")
                ch.dim(f"Retrying in {delay:.1f}s...")

                # Wait before retry
                time.sleep(delay)

        # Should never reach here, but raise last exception if we do
        raise last_exception


def with_retry(config: Optional[RetryConfig] = None, error_category: str = 'general',
               command_name: str = 'unknown'):
    """Decorator to add retry logic to a function.
    
    Args:
        config: Retry configuration (uses defaults if None)
        error_category: Error category for logging
        command_name: Command name for error logging
        
    Example:
        @with_retry(RetryConfig(max_retries=5, base_delay=2.0), 'tunnel', 'tunnel_start')
        def start_tunnel():
            # ... tunnel start logic ...
            pass
    """
    if config is None:
        config = RetryConfig()  # Use defaults

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            operation = RetryableOperation(func, config, error_category, command_name)
            return operation.execute(*args, **kwargs)
        return wrapper
    return decorator


# Preset configurations for common scenarios

TUNNEL_RETRY_CONFIG = RetryConfig(
    max_retries=5,
    base_delay=1.0,
    max_delay=16.0,  # 1s → 2s → 4s → 8s → 16s
    exponential_base=2.0,
    jitter=True,
    timeout=60.0  # 1 minute overall timeout
)

DATABASE_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=2.0,
    max_delay=10.0,  # 2s → 4s → 8s (capped at 10s)
    exponential_base=2.0,
    jitter=True,
    timeout=30.0  # 30 seconds overall timeout
)

FILE_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=1.0,
    max_delay=8.0,  # 1s → 2s → 4s → 8s
    exponential_base=2.0,
    jitter=True,
    timeout=120.0  # 2 minutes for large files
)

NETWORK_RETRY_CONFIG = RetryConfig(
    max_retries=4,
    base_delay=0.5,
    max_delay=15.0,  # 0.5s → 1s → 2s → 4s → 8s (capped at 15s)
    exponential_base=2.0,
    jitter=True,
    timeout=45.0  # 45 seconds overall timeout
)


class CircuitBreaker:
    """Circuit breaker pattern for failing operations.
    
    Prevents repeated attempts to operations that are likely to fail.
    States: CLOSED (normal), OPEN (failing), HALF_OPEN (testing recovery).
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0,
                 success_threshold: int = 2):
        """Initialize circuit breaker.
        
        Args:
            failure_threshold: Failures before opening circuit
            recovery_timeout: Seconds before trying again (OPEN → HALF_OPEN)
            success_threshold: Successes needed in HALF_OPEN to close circuit
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self.state = 'CLOSED'  # CLOSED, OPEN, HALF_OPEN
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None

    def call(self, operation: Callable, *args, **kwargs) -> Any:
        """Execute operation through circuit breaker.
        
        Args:
            operation: Function to execute
            
        Returns:
            Operation result
            
        Raises:
            CircuitBreakerOpenError: If circuit is open
            Original exception: If operation fails
        """
        # Check if circuit is OPEN and recovery timeout has passed
        if self.state == 'OPEN':
            if self.last_failure_time:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.recovery_timeout:
                    ch.dim("Circuit breaker: Testing recovery (HALF_OPEN)")
                    self.state = 'HALF_OPEN'
                    self.success_count = 0
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker is OPEN. "
                        f"Retry in {self.recovery_timeout - elapsed:.0f}s"
                    )

        try:
            # Attempt operation
            result = operation(*args, **kwargs)

            # Success - update state
            self._on_success()
            return result

        except Exception:
            # Failure - update state
            self._on_failure()
            raise

    def _on_success(self):
        """Handle successful operation."""
        if self.state == 'HALF_OPEN':
            self.success_count += 1

            if self.success_count >= self.success_threshold:
                ch.success("Circuit breaker: Service recovered (CLOSED)")
                self.state = 'CLOSED'
                self.failure_count = 0
                self.success_count = 0

        elif self.state == 'CLOSED':
            # Reset failure count on success
            self.failure_count = 0

    def _on_failure(self):
        """Handle failed operation."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == 'HALF_OPEN':
            # Failed during recovery test - back to OPEN
            ch.warning("Circuit breaker: Recovery failed (OPEN)")
            self.state = 'OPEN'
            self.success_count = 0

        elif self.state == 'CLOSED':
            if self.failure_count >= self.failure_threshold:
                ch.error(
                    f"Circuit breaker: Too many failures "
                    f"({self.failure_count}) - Opening circuit"
                )
                self.state = 'OPEN'

    def reset(self):
        """Manually reset circuit breaker to CLOSED state."""
        ch.info("Circuit breaker: Manual reset (CLOSED)")
        self.state = 'CLOSED'
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


# Global circuit breakers for different operation types
_tunnel_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
_database_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
_ssh_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=45.0)


def get_tunnel_circuit_breaker() -> CircuitBreaker:
    """Get global tunnel circuit breaker."""
    return _tunnel_circuit_breaker


def get_database_circuit_breaker() -> CircuitBreaker:
    """Get global database circuit breaker."""
    return _database_circuit_breaker


def get_ssh_circuit_breaker() -> CircuitBreaker:
    """Get global SSH circuit breaker."""
    return _ssh_circuit_breaker
