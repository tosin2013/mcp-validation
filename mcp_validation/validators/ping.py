"""Ping protocol validator for MCP servers."""

import asyncio
import time

from .base import BaseValidator, ValidationContext, ValidatorResult


class PingValidator(BaseValidator):
    """Validates optional ping protocol functionality."""

    @property
    def name(self) -> str:
        return "ping"

    @property
    def description(self) -> str:
        return "Test optional ping protocol functionality"

    @property
    def dependencies(self) -> list[str]:
        return ["protocol"]  # Depends on basic protocol being established

    def is_applicable(self, context: ValidationContext) -> bool:
        """Ping is always applicable if enabled."""
        return self.enabled

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute ping validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {"supported": False, "response_time_ms": None, "error": None}

        try:
            # Send ping request and measure response time
            request_start = time.time()

            timeout = self.config.get("timeout", 5.0)
            response = await context.transport.send_and_receive("ping", timeout=timeout)

            request_end = time.time()
            response_time_ms = (request_end - request_start) * 1000

            if "error" in response:
                error_code = response["error"].get("code", 0)
                error_message = response["error"].get("message", "Unknown error")

                if error_code == -32601:  # Method not found
                    data["error"] = "Method not supported"
                    warnings.append("Ping protocol not supported by server (optional feature)")
                else:
                    data["error"] = f"Error {error_code}: {error_message}"
                    warnings.append(f"Ping protocol error: {error_message}")

            elif "result" in response:
                # Ping supported and successful
                data["supported"] = True
                data["response_time_ms"] = round(response_time_ms, 2)

                # Check response time threshold if configured
                max_response_time = self.config.get("max_response_time_ms", 1000)
                if response_time_ms > max_response_time:
                    warnings.append(
                        f"Ping response time ({response_time_ms:.2f}ms) exceeds "
                        f"threshold ({max_response_time}ms)"
                    )

            else:
                data["error"] = "Invalid ping response format"
                warnings.append("Ping response missing result or error field")

        except asyncio.TimeoutError:
            data["error"] = "Ping request timed out"
            warnings.append("Ping request timed out (optional feature)")
        except Exception as e:
            data["error"] = f"Ping test failed: {str(e)}"
            warnings.append(f"Ping test failed: {str(e)}")

        execution_time = time.time() - start_time

        # Ping validator never fails validation - only provides warnings
        return ValidatorResult(
            validator_name=self.name,
            passed=True,  # Always passes since ping is optional
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )
