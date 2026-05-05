"""Executor — Phase 7. Runs ``action.dsl.yaml`` against the real desktop."""

from dare.executor.executor import Executor, ExecutionResult
from dare.executor.retry import RetryStrategy

__all__ = ["Executor", "ExecutionResult", "RetryStrategy"]
