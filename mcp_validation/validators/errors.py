"""Error compliance validator for MCP servers."""

import asyncio
import json
import time
from typing import Any

from .base import BaseValidator, ValidationContext, ValidatorResult


class ErrorComplianceValidator(BaseValidator):
    """Validates MCP error response compliance with JSON-RPC 2.0 standards."""

    @property
    def name(self) -> str:
        return "errors"

    @property
    def description(self) -> str:
        return "Test MCP error response compliance with JSON-RPC 2.0"

    @property
    def dependencies(self) -> list[str]:
        return ["protocol"]  # Needs basic protocol established

    def is_applicable(self, context: ValidationContext) -> bool:
        """Error compliance testing is always applicable if enabled."""
        return self.enabled

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute error compliance validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "invalid_method_test": {"passed": False, "error": None, "details": None},
            "malformed_request_test": {"passed": False, "error": None, "details": None},
            "compliance_issues": [],
        }

        # Test 1: Invalid method call
        if self.config.get("test_invalid_methods", True):
            await self._test_invalid_method_error(context, warnings, data)

        # Test 2: Malformed JSON-RPC request
        if self.config.get("test_malformed_requests", True):
            await self._test_malformed_request_error(context, warnings, data)

        execution_time = time.time() - start_time

        # Error compliance validator provides warnings but doesn't fail validation
        return ValidatorResult(
            validator_name=self.name,
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    async def _test_invalid_method_error(
        self, context: ValidationContext, warnings: list[str], data: dict[str, Any]
    ) -> None:
        """Test error response for invalid method calls."""
        try:
            response: dict[str, Any] = await context.transport.send_and_receive(
                "invalid_method_that_does_not_exist",
                params={},
                timeout=self.config.get("timeout", 5.0),
            )

            # Validate error response structure
            if "error" in response:
                error = response["error"]

                # Check required fields per MCP specification
                compliance_issues = []

                if "code" not in error:
                    compliance_issues.append("Missing required 'code' field in error")
                elif not isinstance(error["code"], int):
                    compliance_issues.append("Error 'code' must be an integer")

                if "message" not in error:
                    compliance_issues.append("Missing required 'message' field in error")
                elif not isinstance(error["message"], str):
                    compliance_issues.append("Error 'message' must be a string")

                # Check for standard JSON-RPC error codes
                error_code = error.get("code")
                if error_code == -32601:  # Method not found
                    data["invalid_method_test"]["passed"] = True
                elif isinstance(error_code, int):
                    if self.config.get("strict_error_codes", False):
                        warnings.append(
                            f"Non-standard error code {error_code} for invalid method "
                            f"(JSON-RPC 2.0 recommends -32601)"
                        )
                    data["invalid_method_test"]["passed"] = True

                data["invalid_method_test"]["details"] = {
                    "code": error_code,
                    "message": error.get("message", ""),
                    "data": error.get("data"),
                }

                if compliance_issues:
                    data["compliance_issues"].extend(compliance_issues)
                    for issue in compliance_issues:
                        warnings.append(f"Error compliance issue: {issue}")

            elif "result" in response:
                # Server returned success for invalid method - this is incorrect
                data["invalid_method_test"]["error"] = "Server returned success for invalid method"
                warnings.append(
                    "Server returned success for invalid method call (should return JSON-RPC error)"
                )
            else:
                data["invalid_method_test"]["error"] = "Invalid response format"
                warnings.append("Invalid method response missing both 'result' and 'error' fields")

        except asyncio.TimeoutError:
            data["invalid_method_test"]["error"] = "Timeout"
            warnings.append("Invalid method error test timed out")
        except Exception as e:
            data["invalid_method_test"]["error"] = str(e)
            warnings.append(f"Invalid method error test failed: {str(e)}")

    async def _test_malformed_request_error(
        self, context: ValidationContext, warnings: list[str], data: dict[str, Any]
    ) -> None:
        """Test error response for malformed JSON-RPC requests."""
        # This test only works with stdio transport where we can send raw malformed JSON
        # HTTP/SSE transports use MCP SDK which validates JSON before sending
        if not context.process or not hasattr(context.process, "stdin"):
            data["malformed_request_test"]["error"] = None
            data["malformed_request_test"]["skipped"] = True
            data["malformed_request_test"]["reason"] = "Only applicable to stdio transport"
            return

        try:
            # Send malformed JSON request
            malformed_request = '{"jsonrpc": "2.0", "method": "test", "id": 1, "invalid_field":}\n'

            context.process.stdin.write(malformed_request.encode())
            await context.process.stdin.drain()

            # Read responses until we get one that's not a server-initiated message
            # or timeout waiting for the error response
            # Use shorter timeout since many servers silently ignore malformed JSON
            timeout = self.config.get("malformed_timeout", 2.0)
            start_time = asyncio.get_event_loop().time()
            response = None

            while True:
                remaining_timeout = timeout - (asyncio.get_event_loop().time() - start_time)
                if remaining_timeout <= 0:
                    # Timeout - server didn't respond to malformed request
                    # This is acceptable behavior - servers may silently ignore malformed JSON
                    data["malformed_request_test"]["error"] = None
                    data["malformed_request_test"]["ignored"] = True
                    if self.config.get("strict_malformed_handling", False):
                        warnings.append(
                            "Server did not respond to malformed JSON-RPC request (strict mode: should return parse error -32700)"
                        )
                    return

                try:
                    response_line = await asyncio.wait_for(
                        context.process.stdout.readline(), timeout=remaining_timeout
                    )
                except asyncio.TimeoutError:
                    # Timeout waiting for response - server silently ignored malformed request
                    # This is acceptable behavior
                    data["malformed_request_test"]["error"] = None
                    data["malformed_request_test"]["ignored"] = True
                    if self.config.get("strict_malformed_handling", False):
                        warnings.append(
                            "Server did not respond to malformed JSON-RPC request (strict mode: should return parse error -32700)"
                        )
                    return

                # Try to parse response
                try:
                    parsed_response = context.transport.parse_response(response_line.decode())

                    # Skip server-initiated requests/notifications (have 'method' field)
                    if "method" in parsed_response:
                        continue

                    # This looks like a response to our malformed request
                    response = parsed_response
                    break

                except json.JSONDecodeError:
                    # Server sent invalid JSON - could be response to malformed request
                    data["malformed_request_test"]["error"] = "Server sent invalid JSON response"
                    warnings.append("Server sent invalid JSON response to malformed request")
                    return

            # Check if we got an error response
            if response and "error" in response:
                error = response["error"]
                error_code = error.get("code")

                # Check for parse error code
                if error_code == -32700:  # Parse error
                    data["malformed_request_test"]["passed"] = True
                elif isinstance(error_code, int):
                    if self.config.get("strict_error_codes", False):
                        warnings.append(
                            f"Non-standard error code {error_code} for malformed JSON "
                            f"(JSON-RPC 2.0 recommends -32700)"
                        )
                    data["malformed_request_test"]["passed"] = True

                data["malformed_request_test"]["details"] = {
                    "code": error_code,
                    "message": error.get("message", ""),
                    "data": error.get("data"),
                }
            else:
                # Got a response but no error field - server processed malformed JSON as valid
                data["malformed_request_test"][
                    "error"
                ] = "Server processed malformed JSON as valid request"
                if self.config.get("strict_malformed_handling", False):
                    warnings.append(
                        "Server processed malformed JSON-RPC request (strict mode: should return parse error -32700)"
                    )
                else:
                    # In non-strict mode, just note it
                    data["malformed_request_test"]["processed_as_valid"] = True

        except Exception as e:
            data["malformed_request_test"]["error"] = str(e)
            warnings.append(f"Malformed request error test failed: {str(e)}")
