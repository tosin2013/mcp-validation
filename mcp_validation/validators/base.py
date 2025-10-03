"""Base validator interface for MCP validation plugins."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core.transport import MCPTransport


@dataclass
class ValidationContext:
    """Context passed to validators containing transport and shared state."""

    server_info: dict[str, Any]
    capabilities: dict[str, Any]
    timeout: float = 30.0
    command_args: list[str] | None = None
    transport: MCPTransport | None = None
    # Optional process for stdio transport compatibility
    process: asyncio.subprocess.Process | None = None
    # New fields for HTTP transport
    endpoint: str | None = None
    transport_type: str = "stdio"
    # Discovered items from capabilities validator
    discovered_tools: list[str] = field(default_factory=list)
    discovered_resources: list[str] = field(default_factory=list)
    discovered_prompts: list[str] = field(default_factory=list)


@dataclass
class ValidatorResult:
    """Result from a validator execution."""

    validator_name: str
    passed: bool
    errors: list[str]
    warnings: list[str]
    data: dict[str, Any]
    execution_time: float


class BaseValidator(ABC):
    """Base class for all MCP validators."""

    def __init__(self, config: dict[str, Any] = None):
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
    def dependencies(self) -> list[str]:
        """List of validator names this validator depends on."""
        return []

    @abstractmethod
    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute the validation logic."""
        pass

    def is_applicable(self, context: ValidationContext) -> bool:
        """Check if this validator should run given the context."""
        return self.enabled

    def configure(self, config: dict[str, Any]) -> None:
        """Update validator configuration."""
        self.config.update(config)
        self.enabled = self.config.get("enabled", True)
        self.required = self.config.get("required", False)
