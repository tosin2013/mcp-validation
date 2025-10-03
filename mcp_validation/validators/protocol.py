"""MCP protocol compliance validator."""

import asyncio
import time
from typing import Any, Dict, List

from .base import BaseValidator, ValidationContext, ValidatorResult


class ProtocolValidator(BaseValidator):
    """Validates basic MCP protocol compliance."""

    @property
    def name(self) -> str:
        return "protocol"

    @property
    def description(self) -> str:
        return "Basic MCP protocol compliance validation"

    @property
    def dependencies(self) -> List[str]:
        return []  # No dependencies - this is the foundation

    def is_applicable(self, context: ValidationContext) -> bool:
        """Protocol validation is always applicable."""
        return self.enabled

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute protocol validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {"server_info": {}, "capabilities": {}, "protocol_version": None}

        try:
            # Step 1: Send initialize request
            success = await self._test_initialize(context, errors, data)
            if not success:
                execution_time = time.time() - start_time
                return ValidatorResult(
                    validator_name=self.name,
                    passed=False,
                    errors=errors,
                    warnings=warnings,
                    data=data,
                    execution_time=execution_time,
                )

            # Step 2: Send initialized notification
            await self._send_initialized(context, errors)

            # Update context with discovered info
            context.server_info.update(data["server_info"])
            context.capabilities.update(data["capabilities"])

        except Exception as e:
            errors.append(f"Protocol validation failed: {str(e)}")

        execution_time = time.time() - start_time

        return ValidatorResult(
            validator_name=self.name,
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    async def _test_initialize(
        self, context: ValidationContext, errors: List[str], data: Dict[str, Any]
    ) -> bool:
        """Test the initialize request/response."""
        try:
            # Send initialize request with latest protocol version
            init_params = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"roots": {"listChanged": True}, "sampling": {}},
                "clientInfo": {"name": "mcp-validator", "version": "2.0.0"},
            }

            response = await context.transport.send_and_receive(
                "initialize", init_params, timeout=context.timeout
            )

            # Validate initialize response
            if "error" in response:
                errors.append(f"Initialize request failed: {response['error']}")
                return False

            if "result" not in response:
                errors.append("Initialize response missing 'result' field")
                return False

            result = response["result"]

            # Validate required fields
            required_fields = ["protocolVersion", "capabilities", "serverInfo"]
            for field in required_fields:
                if field not in result:
                    errors.append(f"Initialize result missing required field: {field}")

            # Store server info and capabilities
            data["server_info"] = result.get("serverInfo", {})
            data["capabilities"] = result.get("capabilities", {})
            data["protocol_version"] = result.get("protocolVersion")

            # Validate protocol version - support multiple MCP versions
            protocol_version = result.get("protocolVersion")
            supported_versions = ["2024-11-05", "2025-03-26", "2025-06-18"]
            if protocol_version not in supported_versions:
                errors.append(f"Unsupported protocol version: {protocol_version} (supported: {', '.join(supported_versions)})")

            return len(errors) == 0

        except asyncio.TimeoutError:
            errors.append("Initialize request timed out")
            return False
        except Exception as e:
            errors.append(f"Initialize request failed: {str(e)}")
            return False

    async def _send_initialized(self, context: ValidationContext, errors: List[str]) -> None:
        """Send the initialized notification."""
        try:
            await context.transport.send_notification("notifications/initialized")
        except Exception as e:
            errors.append(f"Failed to send initialized notification: {str(e)}")
