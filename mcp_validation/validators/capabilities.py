"""MCP capabilities testing validator."""

import asyncio
import time
from typing import Any

from .base import BaseValidator, ValidationContext, ValidatorResult


class CapabilitiesValidator(BaseValidator):
    """Validates MCP server capabilities."""

    @property
    def name(self) -> str:
        return "capabilities"

    @property
    def description(self) -> str:
        return "Test advertised MCP server capabilities"

    @property
    def dependencies(self) -> list[str]:
        return ["protocol"]  # Needs protocol to be established first

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable if server advertises capabilities."""
        return self.enabled and bool(context.capabilities)

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute capabilities validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {"tools": [], "prompts": [], "resources": [], "tested_capabilities": []}

        try:
            # Test each advertised capability
            if "resources" in context.capabilities:
                await self._test_resources_list(context, errors, warnings, data)
                data["tested_capabilities"].append("resources")

            if "tools" in context.capabilities:
                await self._test_tools_list(context, errors, warnings, data)
                data["tested_capabilities"].append("tools")

            if "prompts" in context.capabilities:
                await self._test_prompts_list(context, errors, warnings, data)
                data["tested_capabilities"].append("prompts")

        except Exception as e:
            errors.append(f"Capabilities testing failed: {str(e)}")

        execution_time = time.time() - start_time

        return ValidatorResult(
            validator_name=self.name,
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    async def _test_resources_list(
        self,
        context: ValidationContext,
        errors: list[str],
        warnings: list[str],
        data: dict[str, Any],
    ) -> None:
        """Test resources/list request."""
        await self._test_list_request(
            context, "resources/list", "resources", errors, warnings, data["resources"]
        )

    async def _test_tools_list(
        self,
        context: ValidationContext,
        errors: list[str],
        warnings: list[str],
        data: dict[str, Any],
    ) -> None:
        """Test tools/list request."""
        await self._test_list_request(
            context, "tools/list", "tools", errors, warnings, data["tools"]
        )

    async def _test_prompts_list(
        self,
        context: ValidationContext,
        errors: list[str],
        warnings: list[str],
        data: dict[str, Any],
    ) -> None:
        """Test prompts/list request."""
        await self._test_list_request(
            context, "prompts/list", "prompts", errors, warnings, data["prompts"]
        )

    async def _test_list_request(
        self,
        context: ValidationContext,
        method: str,
        expected_field: str,
        errors: list[str],
        warnings: list[str],
        items_list: list[str],
    ) -> None:
        """Test a generic list request."""
        try:
            response = await context.transport.send_and_receive(
                method, timeout=self.config.get("timeout", 5.0)
            )

            if "error" in response:
                warnings.append(f"{method} request failed: {response['error']}")
            elif "result" not in response:
                warnings.append(f"{method} response missing 'result' field")
            elif expected_field not in response["result"]:
                warnings.append(f"{method} result missing '{expected_field}' field")
            else:
                # Validate that it's a list
                items = response["result"][expected_field]
                if not isinstance(items, list):
                    warnings.append(f"{method} result '{expected_field}' should be a list")
                else:
                    # Extract names from items
                    for item in items:
                        if isinstance(item, dict) and "name" in item:
                            items_list.append(item["name"])
                        elif isinstance(item, str):
                            items_list.append(item)

                    # Limit items if configured
                    max_items = self.config.get("max_items_to_list", 100)
                    if len(items_list) > max_items:
                        items_list[:] = items_list[:max_items]
                        warnings.append(f"Limited {expected_field} list to {max_items} items")

        except asyncio.TimeoutError:
            warnings.append(f"{method} request timed out")
        except Exception as e:
            error_msg = str(e)
            # Provide more helpful context for common errors
            if "Session terminated" in error_msg or "connection" in error_msg.lower():
                warnings.append(
                    f"{method} request failed: Connection lost (server closed session after previous request)"
                )
            else:
                warnings.append(f"{method} request failed: {error_msg}")
