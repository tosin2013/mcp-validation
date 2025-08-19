"""Base validator interface for MCP validation plugins."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from ..core.transport import JSONRPCTransport

@dataclass
class ValidationContext:
    """Context passed to validators containing process and shared state."""

    process: asyncio.subprocess.Process
    server_info: Dict[str, Any]
    capabilities: Dict[str, Any]
    timeout: float = 30.0
    command_args: Optional[List[str]] = None
    transport: Optional[JSONRPCTransport] = None


@dataclass
class ValidatorResult:
    """Result from a validator execution."""

    validator_name: str
    passed: bool
    errors: List[str]
    warnings: List[str]
    data: Dict[str, Any]
    execution_time: float


class BaseValidator(ABC):
    """Base class for all MCP validators."""

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.required = self.config.get("required", False)

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this validator."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this validator tests."""
        pass

    @property
    def dependencies(self) -> List[str]:
        """List of validator names this validator depends on."""
        return []

    @abstractmethod
    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute the validation logic."""
        pass

    def is_applicable(self, context: ValidationContext) -> bool:
        """Check if this validator should run given the context."""
        return self.enabled

    def configure(self, config: Dict[str, Any]) -> None:
        """Update validator configuration."""
        self.config.update(config)
        self.enabled = self.config.get("enabled", True)
        self.required = self.config.get("required", False)
