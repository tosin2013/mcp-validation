"""Configuration management for MCP validation."""

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidatorConfig:
    """Configuration for a specific validator."""

    enabled: bool = True
    required: bool = False
    timeout: float | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationProfile:
    """A validation profile containing multiple validator configurations."""

    name: str
    description: str
    validators: dict[str, ValidatorConfig] = field(default_factory=dict)
    global_timeout: float = 30.0
    continue_on_failure: bool = True
    parallel_execution: bool = False


class ConfigurationManager:
    """Manages validation configurations and profiles."""

    DEFAULT_PROFILES = {
        "basic": ValidationProfile(
            name="basic",
            description="Basic MCP protocol compliance validation",
            validators={
                "protocol": ValidatorConfig(enabled=True, required=True),
                "capabilities": ValidatorConfig(enabled=True, required=False),
            },
        ),
        "comprehensive": ValidationProfile(
            name="comprehensive",
            description="Complete validation including optional features",
            validators={
                "registry": ValidatorConfig(
                    enabled=True,
                    required=True,
                    parameters={
                        "packages": [
                            {"name": "express", "type": "npm"},
                            {"name": "requests", "type": "pypi"},
                            {"name": "node", "type": "docker"},
                        ]
                    },
                ),
                "protocol": ValidatorConfig(enabled=True, required=True),
                "capabilities": ValidatorConfig(enabled=True, required=False),
                "ping": ValidatorConfig(enabled=True, required=False),
                "errors": ValidatorConfig(enabled=True, required=False),
                "security": ValidatorConfig(enabled=True, required=False),
            },
            continue_on_failure=False,
        ),
        "security_focused": ValidationProfile(
            name="security_focused",
            description="Security-focused validation with mcp-scan",
            validators={
                "protocol": ValidatorConfig(enabled=True, required=True),
                "errors": ValidatorConfig(enabled=True, required=False),
                "security": ValidatorConfig(enabled=True, required=True),
            },
        ),
        "development": ValidationProfile(
            name="development",
            description="Development-friendly validation with detailed feedback",
            validators={
                "protocol": ValidatorConfig(enabled=True, required=True),
                "capabilities": ValidatorConfig(enabled=True, required=False),
                "ping": ValidatorConfig(enabled=True, required=False),
                "errors": ValidatorConfig(enabled=True, required=False),
            },
            continue_on_failure=True,
            parallel_execution=False,
        ),
        "fail_fast": ValidationProfile(
            name="fail_fast",
            description="Fail-fast validation: dependencies first, then core MCP",
            validators={
                "registry": ValidatorConfig(
                    enabled=True,
                    required=True,
                    parameters={
                        "packages": [
                            {"name": "express", "type": "npm"},
                            {"name": "requests", "type": "pypi"},
                            {"name": "node", "type": "docker"},
                        ]
                    },
                ),
                "protocol": ValidatorConfig(enabled=True, required=True),
                "capabilities": ValidatorConfig(enabled=True, required=False),
            },
            continue_on_failure=False,
            parallel_execution=False,
        ),
    }

    def __init__(self, config_file: str | None = None):
        self.config_file = config_file
        self.profiles: dict[str, ValidationProfile] = self.DEFAULT_PROFILES.copy()
        self.active_profile: str = "comprehensive"

        if config_file and os.path.exists(config_file):
            self.load_config(config_file)

    def load_config(self, config_file: str) -> None:
        """Load configuration from JSON file."""
        try:
            with open(config_file) as f:
                config_data = json.load(f)

            # Load custom profiles
            if "profiles" in config_data:
                for profile_name, profile_data in config_data["profiles"].items():
                    validators = {}
                    for validator_name, validator_data in profile_data.get(
                        "validators", {}
                    ).items():
                        validators[validator_name] = ValidatorConfig(**validator_data)

                    self.profiles[profile_name] = ValidationProfile(
                        name=profile_name,
                        description=profile_data.get("description", ""),
                        validators=validators,
                        global_timeout=profile_data.get("global_timeout", 30.0),
                        continue_on_failure=profile_data.get("continue_on_failure", True),
                        parallel_execution=profile_data.get("parallel_execution", False),
                    )

            # Set active profile
            if "active_profile" in config_data:
                self.active_profile = config_data["active_profile"]

        except Exception as e:
            raise ValueError(f"Failed to load configuration from {config_file}: {e}") from e

    def save_config(self, config_file: str) -> None:
        """Save current configuration to JSON file."""
        config_data = {"active_profile": self.active_profile, "profiles": {}}

        for profile_name, profile in self.profiles.items():
            if profile_name not in self.DEFAULT_PROFILES:  # Only save custom profiles
                validators = {}
                for validator_name, validator_config in profile.validators.items():
                    validators[validator_name] = {
                        "enabled": validator_config.enabled,
                        "required": validator_config.required,
                        "timeout": validator_config.timeout,
                        "parameters": validator_config.parameters,
                    }

                config_data["profiles"][profile_name] = {
                    "description": profile.description,
                    "validators": validators,
                    "global_timeout": profile.global_timeout,
                    "continue_on_failure": profile.continue_on_failure,
                    "parallel_execution": profile.parallel_execution,
                }

        with open(config_file, "w") as f:
            json.dump(config_data, f, indent=2)

    def get_active_profile(self) -> ValidationProfile:
        """Get the currently active validation profile."""
        return self.profiles[self.active_profile]

    def set_active_profile(self, profile_name: str) -> None:
        """Set the active validation profile."""
        if profile_name not in self.profiles:
            raise ValueError(f"Profile '{profile_name}' not found")
        self.active_profile = profile_name

    def list_profiles(self) -> list[str]:
        """List all available profile names."""
        return list(self.profiles.keys())

    def create_profile(self, profile: ValidationProfile) -> None:
        """Create or update a validation profile."""
        self.profiles[profile.name] = profile

    def get_validator_config(self, validator_name: str) -> ValidatorConfig | None:
        """Get configuration for a specific validator in the active profile."""
        active_profile = self.get_active_profile()
        return active_profile.validators.get(validator_name)


def load_config_from_env() -> ConfigurationManager:
    """Load configuration from environment variables and default locations."""
    # Check for config file in environment
    config_file = os.environ.get("MCP_VALIDATION_CONFIG")

    # Check for config file in standard locations
    if not config_file:
        possible_locations = [
            ".mcp-validation.json",
            os.path.expanduser("~/.mcp-validation.json"),
            "/etc/mcp-validation.json",
        ]
        for location in possible_locations:
            if os.path.exists(location):
                config_file = location
                break

    config_manager = ConfigurationManager(config_file)

    # Override active profile from environment
    if "MCP_VALIDATION_PROFILE" in os.environ:
        profile_name = os.environ["MCP_VALIDATION_PROFILE"]
        if profile_name in config_manager.profiles:
            config_manager.set_active_profile(profile_name)

    return config_manager
