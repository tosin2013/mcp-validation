"""Core validation orchestrator for MCP servers."""

import asyncio
import time
from typing import Any

from ..config.settings import ConfigurationManager, ValidationProfile
from ..utils.debug import (
    log_execution_result,
    log_execution_start,
    log_execution_step,
    log_validation_summary,
    log_validator_progress,
    set_debug_enabled,
    set_verbose_enabled,
    verbose_log,
)
from ..validators.base import BaseValidator, ValidationContext, ValidatorResult
from .result import ValidationSession


def _inject_container_env_vars(command_args: list[str], env_vars: dict[str, str]) -> list[str]:
    """Inject environment variables as -e options for container commands."""
    if not env_vars or len(command_args) < 2:
        return command_args

    # Check if this is a container run command
    if command_args[0] not in ["docker", "podman"] or command_args[1] != "run":
        return command_args

    # Find insertion point (after 'run' but before the image name)
    # We need to insert after options but before the image
    insertion_point = 2

    # Skip existing options to find where image starts
    options_with_values = {
        "-v",
        "--volume",
        "-e",
        "--env",
        "-p",
        "--port",
        "--name",
        "-w",
        "--workdir",
        "-u",
        "--user",
        "--entrypoint",
        "--hostname",
        "--restart",
        "--memory",
        "--cpus",
        "--network",
        "--label",
    }

    i = 2
    while i < len(command_args):
        arg = command_args[i]

        if arg.startswith("-"):
            if arg in options_with_values:
                # Option with separate value
                i += 2
                insertion_point = i
            elif "=" in arg:
                # Option with value in same argument
                i += 1
                insertion_point = i
            else:
                # Flag option
                i += 1
                insertion_point = i
        else:
            # Found the image name
            break

    # Build new command with environment variables injected
    new_command = command_args[:insertion_point]

    # Add environment variables as -e options
    for key, value in env_vars.items():
        new_command.extend(["-e", f"{key}={value}"])

    # Add the rest of the command (image and arguments)
    new_command.extend(command_args[insertion_point:])

    return new_command


class ValidatorRegistry:
    """Registry for available validators."""

    def __init__(self):
        self._validators: dict[str, type[BaseValidator]] = {}

    def register(self, validator_class: type[BaseValidator]) -> None:
        """Register a validator class."""
        # Create temporary instance to get name
        temp_instance = validator_class()
        self._validators[temp_instance.name] = validator_class

    def get_validator(self, name: str) -> type[BaseValidator] | None:
        """Get validator class by name."""
        return self._validators.get(name)

    def list_validators(self) -> list[str]:
        """List all registered validator names."""
        return list(self._validators.keys())

    def create_validator(self, name: str, config: dict[str, Any] = None) -> BaseValidator | None:
        """Create validator instance with configuration."""
        validator_class = self.get_validator(name)
        if validator_class:
            return validator_class(config)
        return None


class MCPValidationOrchestrator:
    """Orchestrates MCP server validation using configurable validators."""

    def __init__(self, config_manager: ConfigurationManager):
        self.config_manager = config_manager
        self.registry = ValidatorRegistry()
        self._register_builtin_validators()

    def _register_builtin_validators(self) -> None:
        """Register built-in validators."""
        # Import and register built-in validators
        try:
            from ..validators.capabilities import CapabilitiesValidator
            from ..validators.container import ContainerUBIValidator, ContainerVersionValidator
            from ..validators.errors import ErrorComplianceValidator
            from ..validators.ping import PingValidator
            from ..validators.protocol import ProtocolValidator
            from ..validators.registry import RegistryValidator
            from ..validators.repo import LicenseValidator, RepoAvailabilityValidator
            from ..validators.runtime import RuntimeExecutableValidator, RuntimeExistsValidator
            from ..validators.security import SecurityValidator

            # Register repository validators first (they have no dependencies)
            self.registry.register(RepoAvailabilityValidator)
            self.registry.register(LicenseValidator)

            # Register runtime validators (run after repo but before others)
            self.registry.register(RuntimeExistsValidator)
            self.registry.register(RuntimeExecutableValidator)

            # Register container validators (run after runtime validators)
            self.registry.register(ContainerUBIValidator)
            self.registry.register(ContainerVersionValidator)

            # Register other validators
            self.registry.register(ProtocolValidator)
            self.registry.register(CapabilitiesValidator)
            self.registry.register(PingValidator)
            self.registry.register(ErrorComplianceValidator)
            self.registry.register(SecurityValidator)
            self.registry.register(RegistryValidator)
        except ImportError as e:
            # Handle missing validators gracefully
            print(f"Warning: Some validators not available: {e}")

    def register_validator(self, validator_class: type[BaseValidator]) -> None:
        """Register a custom validator."""
        self.registry.register(validator_class)

    async def validate_server(
        self,
        command_args: list[str] | None = None,
        env_vars: dict[str, str] = None,
        profile_name: str | None = None,
        debug: bool = False,
        verbose: bool = False,
        transport_type: str = "stdio",
        endpoint: str | None = None,
        auth_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> ValidationSession:
        """Execute complete validation session against MCP server."""

        # Set debug and verbose state based on CLI flags
        set_debug_enabled(debug)
        set_verbose_enabled(verbose)

        start_time = time.time()
        errors = []
        warnings = []
        validator_results = []
        final_command_args = command_args  # Initialize with original command args
        transport = None
        process = None

        # Use specified profile or active profile
        if profile_name:
            if profile_name not in self.config_manager.profiles:
                raise ValueError(f"Profile '{profile_name}' not found")
            profile = self.config_manager.profiles[profile_name]
        else:
            profile = self.config_manager.get_active_profile()

        try:
            # Log execution start with full context
            log_execution_start(command_args, env_vars)

            # Create transport using factory
            verbose_log(f"Setting up {transport_type} transport...")
            log_execution_step("Creating transport", f"Type: {transport_type}")
            from .transport_factory import TransportFactory

            transport = await TransportFactory.create_transport(
                transport_type=transport_type,
                command_args=command_args,
                endpoint=endpoint,
                env_vars=env_vars,
                auth_token=auth_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            verbose_log("✅ Transport initialized successfully")

            # For stdio transport, we need to get the process for compatibility
            if transport_type == "stdio" and hasattr(transport, "process"):
                process = transport.process
                final_command_args = command_args
                log_execution_step("Process started", f"PID: {process.pid}")
            elif transport_type == "http":
                log_execution_step("HTTP transport initialized", f"Endpoint: {endpoint}")

            # Create validation context
            log_execution_step("Setting up validation context")
            context = ValidationContext(
                server_info={},
                capabilities={},
                timeout=profile.global_timeout,
                command_args=final_command_args,
                transport=transport,
                process=process,
                endpoint=endpoint,
                transport_type=transport_type,
            )

            # Create and configure validators
            verbose_log(f"Loading validation profile: {profile.name}")
            log_execution_step("Creating validators", f"Profile: {profile.name}")
            validators = self._create_validators(profile)
            verbose_log(
                f"📋 Configured {len(validators)} validators: {', '.join([v.name for v in validators])}"
            )
            log_execution_step(
                f"Configured {len(validators)} validators", f"Names: {[v.name for v in validators]}"
            )

            # Execute validators
            verbose_log(f"🚀 Starting validation ({len(validators)} validators)")
            log_execution_step(
                "Starting validation",
                f"Mode: {'parallel' if profile.parallel_execution else 'sequential'}",
            )
            if profile.parallel_execution:
                validator_results = await self._execute_validators_parallel(
                    validators, context, profile
                )
            else:
                validator_results = await self._execute_validators_sequential(
                    validators, context, profile
                )
            verbose_log("🏁 Validation completed")

            # Clean up transport
            log_execution_step("Cleaning up transport")
            if transport:
                await transport.close()
            log_execution_result(True, "Transport cleanup completed")

        except Exception as e:
            error_msg = f"Validation setup failed: {str(e)}"
            errors.append(error_msg)
            log_execution_result(False, error_msg)
        finally:
            # Ensure transport is always cleaned up
            if transport:
                try:
                    await transport.close()
                except Exception:
                    # Ignore cleanup errors
                    pass

        # Determine overall success
        overall_success = self._determine_overall_success(validator_results, profile)

        # Collect errors and warnings
        for result in validator_results:
            errors.extend(result.errors)
            warnings.extend(result.warnings)

        execution_time = time.time() - start_time

        # Log validation summary
        passed_count = sum(1 for r in validator_results if r.passed)
        failed_count = len(validator_results) - passed_count
        log_validation_summary(len(validator_results), passed_count, failed_count, execution_time)

        return ValidationSession(
            profile_name=profile.name,
            overall_success=overall_success,
            execution_time=execution_time,
            validator_results=validator_results,
            errors=errors,
            warnings=warnings,
            command_args=final_command_args,
        )

    def _create_validators(self, profile: ValidationProfile) -> list[BaseValidator]:
        """Create configured validator instances."""
        validators = []

        for validator_name, validator_config in profile.validators.items():
            if not validator_config.enabled:
                continue

            validator = self.registry.create_validator(
                validator_name,
                {
                    "enabled": validator_config.enabled,
                    "required": validator_config.required,
                    "timeout": validator_config.timeout or profile.global_timeout,
                    **validator_config.parameters,
                },
            )

            if validator:
                validators.append(validator)
            else:
                print(f"Warning: Validator '{validator_name}' not found")

        # Sort by dependencies (simple topological sort)
        return self._sort_validators_by_dependencies(validators)

    def _sort_validators_by_dependencies(
        self, validators: list[BaseValidator]
    ) -> list[BaseValidator]:
        """Sort validators by their dependencies with repository validators first."""
        validator_map = {v.name: v for v in validators}
        sorted_validators = []
        processed = set()

        def process_validator(validator: BaseValidator):
            if validator.name in processed:
                return

            # Process dependencies first
            for dep_name in validator.dependencies:
                if dep_name in validator_map:
                    process_validator(validator_map[dep_name])

            sorted_validators.append(validator)
            processed.add(validator.name)

        # First, process repository validators explicitly (they should run first)
        repo_validators = ["repo_availability", "license"]
        for repo_validator_name in repo_validators:
            if repo_validator_name in validator_map:
                process_validator(validator_map[repo_validator_name])

        # Then, process runtime validators (run after repo but before others)
        runtime_validators = ["runtime_exists", "runtime_executable"]
        for runtime_validator_name in runtime_validators:
            if runtime_validator_name in validator_map:
                process_validator(validator_map[runtime_validator_name])

        # Then, process container validators (run after runtime validators)
        container_validators = ["container_ubi", "container_version"]
        for container_validator_name in container_validators:
            if container_validator_name in validator_map:
                process_validator(validator_map[container_validator_name])

        # Then process all remaining validators
        for validator in validators:
            process_validator(validator)

        return sorted_validators

    async def _execute_validators_sequential(
        self,
        validators: list[BaseValidator],
        context: ValidationContext,
        profile: ValidationProfile,
    ) -> list[ValidatorResult]:
        """Execute validators sequentially."""
        results = []
        total_validators = len(validators)

        for i, validator in enumerate(validators, 1):
            if not validator.is_applicable(context):
                verbose_log(f"⏭️  Skipping {validator.name} (not applicable)")
                log_validator_progress(
                    validator.name, "SKIPPED", "Not applicable for current context"
                )
                continue

            verbose_log(f"🔄 Running {validator.name} ({i}/{total_validators})")
            log_validator_progress(validator.name, "STARTING", f"({i}/{total_validators})")
            validator_start_time = time.time()

            try:
                result = await validator.validate(context)
                results.append(result)

                validator_execution_time = time.time() - validator_start_time
                status = "PASSED" if result.passed else "FAILED"
                status_icon = "✅" if result.passed else "❌"
                details = f"Time: {validator_execution_time:.2f}s"
                if result.errors:
                    details += f", Errors: {len(result.errors)}"
                if result.warnings:
                    details += f", Warnings: {len(result.warnings)}"

                verbose_log(
                    f"{status_icon} {validator.name}: {status} ({validator_execution_time:.2f}s)"
                )
                log_validator_progress(validator.name, status, details)

                # Update context with results (for dependent validators)
                if validator.name == "protocol":
                    context.server_info.update(result.data.get("server_info", {}))
                    context.capabilities.update(result.data.get("capabilities", {}))
                    log_validator_progress(
                        validator.name,
                        "CONTEXT_UPDATED",
                        "Server info and capabilities stored for dependent validators",
                    )
                elif validator.name == "capabilities":
                    # Store discovered items for dependent validators (like security)
                    context.discovered_tools = result.data.get("tools", [])
                    context.discovered_resources = result.data.get("resources", [])
                    context.discovered_prompts = result.data.get("prompts", [])
                    log_validator_progress(
                        validator.name,
                        "CONTEXT_UPDATED",
                        f"Discovered items stored: {len(context.discovered_tools)} tools, {len(context.discovered_resources)} resources, {len(context.discovered_prompts)} prompts",
                    )

                # Stop on required validator failure if configured
                if (
                    not profile.continue_on_failure
                    and validator.config.get("required")
                    and not result.passed
                ):
                    log_validator_progress(
                        validator.name,
                        "STOPPING",
                        "Required validator failed and fail-fast is enabled",
                    )
                    break

            except Exception as e:
                validator_execution_time = time.time() - validator_start_time
                error_msg = f"Validator execution failed: {str(e)}"
                log_validator_progress(
                    validator.name,
                    "ERROR",
                    f"Exception after {validator_execution_time:.2f}s: {str(e)}",
                )

                error_result = ValidatorResult(
                    validator_name=validator.name,
                    passed=False,
                    errors=[error_msg],
                    warnings=[],
                    data={},
                    execution_time=validator_execution_time,
                )
                results.append(error_result)

                if not profile.continue_on_failure and validator.config.get("required"):
                    log_validator_progress(
                        validator.name,
                        "STOPPING",
                        "Required validator failed with exception and fail-fast is enabled",
                    )
                    break

        return results

    async def _execute_validators_parallel(
        self,
        validators: list[BaseValidator],
        context: ValidationContext,
        profile: ValidationProfile,
    ) -> list[ValidatorResult]:
        """Execute validators in parallel (where possible)."""
        # For now, implement sequential execution
        # Parallel execution would need more sophisticated dependency handling
        return await self._execute_validators_sequential(validators, context, profile)

    def _determine_overall_success(
        self, validator_results: list[ValidatorResult], profile: ValidationProfile
    ) -> bool:
        """Determine if overall validation was successful."""
        for result in validator_results:
            validator_config = profile.validators.get(result.validator_name)
            if validator_config and validator_config.required and not result.passed:
                return False
        return True

    async def _cleanup_process(self, process: asyncio.subprocess.Process) -> None:
        """Clean up the MCP server process."""
        if process.returncode is None:
            try:
                if process.stdin:
                    process.stdin.close()
                    await process.stdin.wait_closed()
            except Exception:
                pass

            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
