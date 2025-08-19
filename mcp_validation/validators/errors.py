"""Error compliance validator for MCP servers."""

import asyncio
import json
import time
from typing import Any, Dict, List

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
    def dependencies(self) -> List[str]:
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
        self, context: ValidationContext, warnings: List[str], data: Dict[str, Any]
    ) -> None:
        """Test error response for invalid method calls."""
        try:
            response: Dict[str, Any] = await context.transport.send_and_receive(
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
        self, context: ValidationContext, warnings: List[str], data: Dict[str, Any]
    ) -> None:
        """Test error response for malformed JSON-RPC requests."""
        try:
            # Send malformed JSON request
            malformed_request = '{"jsonrpc": "2.0", "method": "test", "id": 1, "invalid_field":}\n'

            context.process.stdin.write(malformed_request.encode())
            await context.process.stdin.drain()

            # Read response
            timeout = self.config.get("timeout", 5.0)
            response_line = await asyncio.wait_for(
                context.process.stdout.readline(), timeout=timeout
            )

            # Try to parse response
            try:
                response = context.transport.parse_response(response_line.decode())

                if "error" in response:
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
                    data["malformed_request_test"]["error"] = "No error response for malformed JSON"
                    warnings.append(
                        "Server should return parse error for malformed JSON-RPC request"
                    )

            except json.JSONDecodeError:
                # Server sent invalid JSON response to malformed request
                data["malformed_request_test"]["error"] = "Server sent invalid JSON response"
                warnings.append("Server sent invalid JSON response to malformed request")

        except asyncio.TimeoutError:
            data["malformed_request_test"]["error"] = "Timeout"
            warnings.append("Malformed request error test timed out")
        except Exception as e:
            data["malformed_request_test"]["error"] = str(e)
            warnings.append(f"Malformed request error test failed: {str(e)}")
