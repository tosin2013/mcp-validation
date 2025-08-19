"""MCP validation framework.

A modern, plugin-based validation framework for MCP (Model Context Protocol) servers.
Provides comprehensive testing of protocol compliance, capabilities, and security.
"""

from typing import Any, Dict, List, Optional

# CLI interface
from .cli.main import cli_main

# Configuration system
from .config.settings import (
    ConfigurationManager,
    ValidationProfile,
    ValidatorConfig,
    load_config_from_env,
)
from .core.result import MCPValidationResult, ValidationSession, ValidatorResult
from .core.transport import JSONRPCTransport

# Core components
from .core.validator import MCPValidationOrchestrator, ValidatorRegistry

# Reporting
from .reporting.console import ConsoleReporter
from .reporting.json_report import JSONReporter

# Validator framework
from .validators.base import BaseValidator, ValidationContext

__version__ = "2.0.0"

__all__ = [
    # Core components
    "MCPValidationOrchestrator",
    "ValidatorRegistry",
    "ValidationSession",
    "MCPValidationResult",
    "ValidatorResult",
    "ValidationContext",
    "JSONRPCTransport",
    # Configuration
    "ConfigurationManager",
    "ValidationProfile",
    "ValidatorConfig",
    "load_config_from_env",
    # Validators
    "BaseValidator",
    # Reporting
    "ConsoleReporter",
    "JSONReporter",
    # CLI
    "cli_main",
    # Convenience function
    "validate_server",
]


async def validate_server(
    command_args: List[str],
    env_vars: Optional[Dict[str, str]] = None,
    profile_name: Optional[str] = None,
    config_file: Optional[str] = None,
) -> ValidationSession:
    """
    Validate an MCP server with the specified configuration.

    This is a convenience function that sets up the validation orchestrator
    and runs a complete validation session.

    Args:
        command_args: Command and arguments to execute the MCP server
        env_vars: Optional environment variables for the server process
        profile_name: Validation profile to use (defaults to 'comprehensive')
        config_file: Path to configuration file (optional)

    Returns:
        ValidationSession containing complete validation results

    Example:
        ```python
        session = await validate_server(["python", "my_server.py"])
        if session.overall_success:
            print("✅ Server is MCP compliant!")
        else:
            print("❌ Validation failed:")
            for error in session.errors:
                print(f"  - {error}")
        ```
    """
    if config_file:
        config_manager = ConfigurationManager(config_file)
    else:
        config_manager = load_config_from_env()

    if profile_name:
        config_manager.set_active_profile(profile_name)

    orchestrator = MCPValidationOrchestrator(config_manager)
    return await orchestrator.validate_server(command_args, env_vars, profile_name)
