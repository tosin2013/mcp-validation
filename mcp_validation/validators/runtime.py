"""Runtime validation for MCP servers."""

import asyncio
import os
import shutil
import stat
import time
from typing import Any

from ..utils.debug import debug_log as _debug_log
from .base import BaseValidator, ValidationContext, ValidatorResult


def debug_log(message: str, level: str = "INFO") -> None:
    """Runtime-specific debug logging wrapper."""
    _debug_log(message, level, "RUNTIME")


class RuntimeExistsValidator(BaseValidator):
    """Validates that the specified runtime command is available in the system PATH."""

    @property
    def name(self) -> str:
        return "runtime_exists"

    @property
    def description(self) -> str:
        return "Validates that the specified runtime command exists in system PATH"

    @property
    def dependencies(self) -> list[str]:
        return []  # No dependencies - runs early

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable if runtime_command is provided in config."""
        runtime_command = self.config.get("runtime_command")
        return self.enabled and runtime_command is not None

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute runtime existence validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "runtime_command": None,
            "runtime_found": False,
            "runtime_path": None,
            "runtime_version": None,
            "path_locations": [],
            "search_paths": [],
        }

        runtime_command = self.config.get("runtime_command")
        if not runtime_command:
            errors.append("Runtime command not specified")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        data["runtime_command"] = runtime_command
        debug_log(f"Checking for runtime command: {runtime_command}")

        # Get system PATH for debugging
        system_path = os.environ.get("PATH", "")
        data["search_paths"] = system_path.split(os.pathsep) if system_path else []
        debug_log(f"Searching in {len(data['search_paths'])} PATH directories")

        try:
            # Use shutil.which to find the command
            runtime_path = shutil.which(runtime_command)

            if runtime_path:
                data["runtime_found"] = True
                data["runtime_path"] = runtime_path
                debug_log(f"Runtime found at: {runtime_path}")

                # Try to get version information
                version_info = await self._get_runtime_version(runtime_command)
                if version_info:
                    data["runtime_version"] = version_info
                    debug_log(f"Runtime version: {version_info}")

                # Find all locations of the command in PATH
                all_locations = self._find_all_runtime_locations(runtime_command)
                data["path_locations"] = all_locations
                if len(all_locations) > 1:
                    warnings.append(
                        f"Multiple versions of {runtime_command} found in PATH: {', '.join(all_locations)}"
                    )

            else:
                data["runtime_found"] = False
                debug_log(f"Runtime command '{runtime_command}' not found in PATH", "ERROR")
                errors.append(f"Runtime command '{runtime_command}' not found in system PATH")

                # Provide helpful suggestions
                suggestions = self._get_installation_suggestions(runtime_command)
                if suggestions:
                    warnings.append(
                        f"Installation suggestions for {runtime_command}: {suggestions}"
                    )

        except Exception as e:
            debug_log(f"Runtime existence check failed with exception: {str(e)}", "ERROR")
            errors.append(f"Runtime existence check failed: {str(e)}")

        execution_time = time.time() - start_time
        passed = len(errors) == 0

        debug_log(
            f"Runtime existence validation completed: passed={passed}, found={data['runtime_found']}"
        )

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    async def _get_runtime_version(self, runtime_command: str) -> str | None:
        """Try to get version information from the runtime."""
        version_commands = [["--version"], ["-v"], ["version"], ["-V"], ["--help"]]  # Last resort

        for version_args in version_commands:
            try:
                debug_log(f"Trying version command: {runtime_command} {' '.join(version_args)}")
                process = await asyncio.create_subprocess_exec(
                    runtime_command,
                    *version_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)

                if process.returncode == 0:
                    # Try stdout first, then stderr
                    output = stdout.decode().strip()
                    if not output:
                        output = stderr.decode().strip()

                    if output:
                        # Take first line and limit length
                        version_line = output.split("\n")[0][:100]
                        debug_log(f"Got version info: {version_line}")
                        return version_line

            except (asyncio.TimeoutError, Exception) as e:
                debug_log(f"Version check failed for {' '.join(version_args)}: {str(e)}", "DEBUG")
                continue

        debug_log("Could not determine runtime version", "WARN")
        return None

    def _find_all_runtime_locations(self, runtime_command: str) -> list[str]:
        """Find all locations of the runtime command in PATH."""
        locations = []
        system_path = os.environ.get("PATH", "")

        for path_dir in system_path.split(os.pathsep):
            if not path_dir:
                continue

            try:
                potential_path = os.path.join(path_dir, runtime_command)

                # Check for executable file
                if os.path.isfile(potential_path) and os.access(potential_path, os.X_OK):
                    locations.append(potential_path)

                # Also check with common extensions on Windows
                if os.name == "nt":
                    for ext in [".exe", ".cmd", ".bat"]:
                        ext_path = potential_path + ext
                        if os.path.isfile(ext_path) and os.access(ext_path, os.X_OK):
                            locations.append(ext_path)

            except Exception:
                continue

        return locations

    def _get_installation_suggestions(self, runtime_command: str) -> str | None:
        """Provide installation suggestions for common runtime commands."""
        suggestions = {
            "uv": "Install with: curl -LsSf https://astral.sh/uv/install.sh | sh",
            "docker": "Install Docker Desktop or use package manager",
            "npx": "Install Node.js which includes npx",
            "node": "Install Node.js from nodejs.org or use package manager",
            "python": "Install Python from python.org or use package manager",
            "python3": "Install Python 3 from python.org or use package manager",
            "pip": "Install pip with: python -m ensurepip --upgrade",
            "java": "Install Java JDK from OpenJDK or Oracle",
            "mvn": "Install Apache Maven",
            "gradle": "Install Gradle build tool",
            "go": "Install Go from golang.org",
            "rust": "Install Rust with: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
            "cargo": "Install Rust which includes Cargo",
        }

        return suggestions.get(runtime_command.lower())


class RuntimeExecutableValidator(BaseValidator):
    """Validates that the runtime command is executable by the current user."""

    @property
    def name(self) -> str:
        return "runtime_executable"

    @property
    def description(self) -> str:
        return "Validates that the runtime command is executable by the current user"

    @property
    def dependencies(self) -> list[str]:
        return ["runtime_exists"]  # Depends on runtime existing

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable if runtime_command is provided in config."""
        runtime_command = self.config.get("runtime_command")
        return self.enabled and runtime_command is not None

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute runtime executable validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "runtime_command": None,
            "executable_check_passed": False,
            "permission_details": {},
            "test_execution_successful": False,
            "test_command_used": None,
            "test_output": None,
            "test_execution_time": 0,
        }

        runtime_command = self.config.get("runtime_command")
        if not runtime_command:
            errors.append("Runtime command not specified")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        data["runtime_command"] = runtime_command
        debug_log(f"Checking executability of runtime command: {runtime_command}")

        try:
            # First check if the runtime exists
            runtime_path = shutil.which(runtime_command)
            if not runtime_path:
                errors.append(f"Runtime command '{runtime_command}' not found in PATH")
                return ValidatorResult(
                    validator_name=self.name,
                    passed=False,
                    errors=errors,
                    warnings=warnings,
                    data=data,
                    execution_time=time.time() - start_time,
                )

            # Check file permissions
            permission_check = self._check_file_permissions(runtime_path)
            data["permission_details"] = permission_check

            if not permission_check["is_executable"]:
                errors.append(
                    f"Runtime command '{runtime_command}' at {runtime_path} is not executable by current user"
                )
            else:
                data["executable_check_passed"] = True
                debug_log("Runtime command has proper execute permissions")

                # Test actual execution
                execution_result = await self._test_runtime_execution(runtime_command)
                data.update(execution_result)

                if not execution_result["test_execution_successful"]:
                    error_msg = execution_result.get("error", "Unknown execution error")
                    errors.append(f"Runtime command execution test failed: {error_msg}")
                else:
                    debug_log("Runtime command executed successfully")

        except Exception as e:
            debug_log(f"Runtime executable validation failed with exception: {str(e)}", "ERROR")
            errors.append(f"Runtime executable validation failed: {str(e)}")

        execution_time = time.time() - start_time
        passed = len(errors) == 0

        debug_log(f"Runtime executable validation completed: passed={passed}")

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    def _check_file_permissions(self, file_path: str) -> dict[str, Any]:
        """Check file permissions and ownership details."""
        result = {
            "file_path": file_path,
            "exists": False,
            "is_file": False,
            "is_executable": False,
            "owner_readable": False,
            "owner_writable": False,
            "owner_executable": False,
            "group_readable": False,
            "group_writable": False,
            "group_executable": False,
            "other_readable": False,
            "other_writable": False,
            "other_executable": False,
            "file_mode": None,
            "file_mode_octal": None,
        }

        try:
            if os.path.exists(file_path):
                result["exists"] = True
                result["is_file"] = os.path.isfile(file_path)

                # Check if file is executable by current user
                result["is_executable"] = os.access(file_path, os.X_OK)

                # Get detailed file mode information
                file_stat = os.stat(file_path)
                file_mode = file_stat.st_mode
                result["file_mode"] = file_mode
                result["file_mode_octal"] = oct(stat.S_IMODE(file_mode))

                # Check individual permission bits
                result["owner_readable"] = bool(file_mode & stat.S_IRUSR)
                result["owner_writable"] = bool(file_mode & stat.S_IWUSR)
                result["owner_executable"] = bool(file_mode & stat.S_IXUSR)
                result["group_readable"] = bool(file_mode & stat.S_IRGRP)
                result["group_writable"] = bool(file_mode & stat.S_IWGRP)
                result["group_executable"] = bool(file_mode & stat.S_IXGRP)
                result["other_readable"] = bool(file_mode & stat.S_IROTH)
                result["other_writable"] = bool(file_mode & stat.S_IWOTH)
                result["other_executable"] = bool(file_mode & stat.S_IXOTH)

                debug_log(f"File permissions for {file_path}: {result['file_mode_octal']}")

        except Exception as e:
            debug_log(f"Failed to check file permissions: {str(e)}", "ERROR")

        return result

    async def _test_runtime_execution(self, runtime_command: str) -> dict[str, Any]:
        """Test actual execution of the runtime command."""
        result = {
            "test_execution_successful": False,
            "test_command_used": None,
            "test_output": None,
            "test_error_output": None,
            "test_execution_time": 0,
            "test_exit_code": None,
            "error": None,
        }

        # Define test commands for different runtimes
        test_commands = {
            "uv": ["--version"],
            "docker": ["--version"],
            "npx": ["--version"],
            "node": ["--version"],
            "python": ["--version"],
            "python3": ["--version"],
            "pip": ["--version"],
            "java": ["-version"],
            "mvn": ["--version"],
            "gradle": ["--version"],
            "go": ["version"],
            "cargo": ["--version"],
            "rust": ["--version"],
        }

        # Get appropriate test command or use default
        test_args = test_commands.get(runtime_command.lower(), ["--help"])
        result["test_command_used"] = f"{runtime_command} {' '.join(test_args)}"

        try:
            debug_log(f"Testing runtime execution: {result['test_command_used']}")
            test_start = time.time()

            process = await asyncio.create_subprocess_exec(
                runtime_command,
                *test_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            timeout = self.config.get("execution_timeout", 10.0)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            test_end = time.time()
            result["test_execution_time"] = round(test_end - test_start, 3)
            result["test_exit_code"] = process.returncode

            # Decode output
            stdout_text = stdout.decode("utf-8", errors="ignore").strip()
            stderr_text = stderr.decode("utf-8", errors="ignore").strip()

            result["test_output"] = stdout_text[:500] if stdout_text else None  # Limit output size
            result["test_error_output"] = stderr_text[:500] if stderr_text else None

            # Consider successful if exit code is 0 or if we got some output
            if process.returncode == 0 or stdout_text or stderr_text:
                result["test_execution_successful"] = True
                debug_log(f"Runtime execution test passed in {result['test_execution_time']}s")
            else:
                result["error"] = f"Command exited with code {process.returncode} and no output"
                debug_log(f"Runtime execution test failed: {result['error']}", "ERROR")

        except asyncio.TimeoutError:
            result["error"] = f"Execution test timed out after {timeout} seconds"
            debug_log("Runtime execution test timed out", "ERROR")
        except Exception as e:
            result["error"] = f"Execution test failed: {str(e)}"
            debug_log(f"Runtime execution test failed with exception: {str(e)}", "ERROR")

        return result
