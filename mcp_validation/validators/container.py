"""Container image validation for MCP servers."""

import asyncio
import json
import re
import time
from typing import Any

from ..utils.debug import debug_log as _debug_log
from .base import BaseValidator, ValidationContext, ValidatorResult


def debug_log(message: str, level: str = "INFO") -> None:
    """Container-specific debug logging wrapper."""
    _debug_log(message, level, "CONTAINER")


class ContainerUBIValidator(BaseValidator):
    """Validates that container images are based on UBI (Universal Base Image) with RHEL 9 or 10."""

    @property
    def name(self) -> str:
        return "container_ubi"

    @property
    def description(self) -> str:
        return "Validates that container images use UBI base images with RHEL 9 or 10 (configurable: warns by default, can fail in strict mode)"

    @property
    def dependencies(self) -> list[str]:
        return ["runtime_exists"]  # Depends on docker/podman existing

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable for container runtime commands."""
        return self.enabled and self._is_container_command(context.command_args)

    def _is_container_command(self, command_args: list[str]) -> bool:
        """Check if command is a container runtime command."""
        if not command_args:
            return False

        first_cmd = command_args[0]
        if first_cmd in ["docker", "podman"]:
            # Check if it's a run command with an image
            if len(command_args) >= 3 and command_args[1] == "run":
                return True

        return False

    def _extract_image_name(self, command_args: list[str]) -> str | None:
        """Extract container image name from command arguments."""
        if not self._is_container_command(command_args):
            return None

        # Find the image name in docker/podman run command
        # Format: docker/podman run [options] IMAGE [command]
        _ = command_args[0]  # docker or podman
        if len(command_args) < 3 or command_args[1] != "run":
            return None

        # Options that take values
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

        # Skip run and look for the image (skip options that start with -)
        i = 2
        while i < len(command_args):
            arg = command_args[i]

            # Skip options and their values
            if arg.startswith("-"):
                # Check if this option takes a value
                if arg in options_with_values:
                    # Skip the option and its value
                    i += 2
                    continue
                elif "=" in arg:
                    # Option with value in same argument (like --env=VAR=value)
                    i += 1
                    continue
                else:
                    # Option without value (like --rm, -i, etc.)
                    i += 1
                    continue
            else:
                # First non-option argument should be the image
                return arg

        return None

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute UBI base image validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "container_runtime": None,
            "image_name": None,
            "image_inspected": False,
            "base_image": None,
            "is_ubi_based": False,
            "rhel_version": None,
            "ubi_details": {},
            "inspection_output": None,
        }

        # Extract runtime and image
        runtime = context.command_args[0] if context.command_args else None
        image_name = self._extract_image_name(context.command_args)

        data["container_runtime"] = runtime
        data["image_name"] = image_name

        if not image_name:
            errors.append("Could not extract container image name from command")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        debug_log(f"Validating UBI compliance for image: {image_name}")

        try:
            # Inspect the container image
            inspection_result = await self._inspect_image(runtime, image_name)
            data.update(inspection_result)

            if not inspection_result.get("image_inspected", False):
                errors.append(f"Failed to inspect container image: {image_name}")
                return ValidatorResult(
                    validator_name=self.name,
                    passed=False,
                    errors=errors,
                    warnings=warnings,
                    data=data,
                    execution_time=time.time() - start_time,
                )

            # Check if it's UBI-based
            ubi_check_result = self._check_ubi_compliance(inspection_result)
            data.update(ubi_check_result)

            base_image = data.get("base_image", "Unknown")
            debug_log(f"Base image detected: {base_image}")

            if not data.get("is_ubi_based", False):
                # Check if we should warn or fail for non-UBI images
                warn_only = self.config.get("warn_only_for_non_ubi", True)
                message = f"Container image '{image_name}' is not based on a UBI (Universal Base Image). Base image: {base_image}. Consider using a UBI-based image for better security and support."

                if warn_only:
                    warnings.append(message)
                else:
                    errors.append(message)
            else:
                rhel_version = data.get("rhel_version")
                debug_log(f"UBI-based image detected with RHEL version: {rhel_version}")

                if rhel_version == "9":
                    warnings.append(
                        "Container uses RHEL 9 UBI base image. Consider upgrading to RHEL 10 for latest features and security updates."
                    )
                elif rhel_version == "10":
                    debug_log("Container uses recommended RHEL 10 UBI base image")
                elif rhel_version:
                    warnings.append(
                        f"Container uses RHEL {rhel_version} UBI base image. RHEL 9 or 10 are recommended."
                    )
                else:
                    warnings.append(
                        "Container is UBI-based but RHEL version could not be determined"
                    )

        except Exception as e:
            debug_log(f"UBI validation failed with exception: {str(e)}", "ERROR")
            errors.append(f"UBI validation failed: {str(e)}")

        execution_time = time.time() - start_time
        passed = len(errors) == 0

        debug_log(
            f"UBI validation completed: passed={passed}, is_ubi_based={data.get('is_ubi_based', False)}"
        )

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    async def _inspect_image(self, runtime: str, image_name: str) -> dict[str, Any]:
        """Inspect container image to get metadata."""
        result = {
            "image_inspected": False,
            "inspection_output": None,
            "image_labels": {},
            "image_env": [],
            "error": None,
        }

        try:
            debug_log(f"Inspecting image with {runtime}: {image_name}")

            # Try to pull the image first (if it's not local)
            pull_process = await asyncio.create_subprocess_exec(
                runtime,
                "pull",
                image_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            await asyncio.wait_for(pull_process.communicate(), timeout=60.0)
            debug_log(f"Image pull completed with exit code: {pull_process.returncode}")

            # Inspect the image
            inspect_process = await asyncio.create_subprocess_exec(
                runtime,
                "inspect",
                image_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(inspect_process.communicate(), timeout=30.0)

            if inspect_process.returncode == 0:
                inspect_data = json.loads(stdout.decode())
                if inspect_data and len(inspect_data) > 0:
                    image_data = inspect_data[0]
                    result["image_inspected"] = True
                    result["inspection_output"] = json.dumps(image_data, indent=2)[
                        :2000
                    ]  # Limit size

                    # Extract labels and environment
                    config = image_data.get("Config", {})
                    result["image_labels"] = config.get("Labels", {}) or {}
                    result["image_env"] = config.get("Env", []) or []

                    debug_log(
                        f"Image inspection successful, found {len(result['image_labels'])} labels"
                    )
                else:
                    result["error"] = "Empty inspection result"
            else:
                error_output = stderr.decode().strip()
                result["error"] = f"Inspection failed: {error_output}"
                debug_log(f"Image inspection failed: {error_output}", "ERROR")

        except asyncio.TimeoutError:
            result["error"] = "Image inspection timed out"
            debug_log("Image inspection timed out", "ERROR")
        except json.JSONDecodeError as e:
            result["error"] = f"Failed to parse inspection JSON: {str(e)}"
            debug_log(f"JSON parsing failed: {str(e)}", "ERROR")
        except Exception as e:
            result["error"] = f"Image inspection failed: {str(e)}"
            debug_log(f"Image inspection failed with exception: {str(e)}", "ERROR")

        return result

    def _check_ubi_compliance(self, inspection_result: dict[str, Any]) -> dict[str, Any]:
        """Check if the image is UBI-compliant based on inspection data."""
        result = {
            "is_ubi_based": False,
            "rhel_version": None,
            "base_image": "Unknown",
            "ubi_details": {},
        }

        if not inspection_result.get("image_inspected", False):
            return result

        labels = inspection_result.get("image_labels", {})
        env_vars = inspection_result.get("image_env", [])

        # Check labels for UBI indicators
        _ = [
            "com.redhat.component",
            "io.openshift.tags",
            "release",
            "distribution-scope",
            "name",
            "summary",
            "description",
        ]

        debug_log(f"Checking {len(labels)} labels for UBI indicators")

        for label, value in labels.items():
            if any(indicator in label.lower() for indicator in ["redhat", "ubi", "rhel"]):
                result["ubi_details"][label] = value
                debug_log(f"Found UBI-related label: {label}={value}")

        # Check for specific UBI patterns
        component = labels.get("com.redhat.component", "").lower()
        name = labels.get("name", "").lower()
        summary = labels.get("summary", "").lower()
        description = labels.get("description", "").lower()

        # Determine base image name
        if name:
            result["base_image"] = labels.get("name", "Unknown")
        elif component:
            result["base_image"] = labels.get("com.redhat.component", "Unknown")

        # Check for UBI patterns
        ubi_patterns = [
            r"ubi\d*",  # ubi8, ubi9, ubi10, etc.
            r"universal.base.image",
            r"red.hat.universal.base.image",
        ]

        all_text = f"{component} {name} {summary} {description}".lower()

        for pattern in ubi_patterns:
            if re.search(pattern, all_text):
                result["is_ubi_based"] = True
                debug_log(f"UBI pattern matched: {pattern}")
                break

        # Extract RHEL version
        version_patterns = [r"rhel\s*(\d+)", r"ubi(\d+)", r"red.hat.enterprise.linux.(\d+)"]

        for pattern in version_patterns:
            match = re.search(pattern, all_text)
            if match:
                result["rhel_version"] = match.group(1)
                debug_log(f"RHEL version detected: {result['rhel_version']}")
                break

        # Additional checks in environment variables
        for env_var in env_vars:
            if "redhat" in env_var.lower() or "ubi" in env_var.lower():
                result["ubi_details"]["env_" + env_var.split("=")[0]] = env_var
                if not result["is_ubi_based"]:
                    result["is_ubi_based"] = True
                    debug_log(f"UBI indicator found in environment: {env_var}")

        debug_log(
            f"UBI compliance check result: is_ubi_based={result['is_ubi_based']}, rhel_version={result['rhel_version']}"
        )
        return result


class ContainerVersionValidator(BaseValidator):
    """Validates that container images use the latest available version of the software."""

    @property
    def name(self) -> str:
        return "container_version"

    @property
    def description(self) -> str:
        return "Validates that container images use the latest available version"

    @property
    def dependencies(self) -> list[str]:
        return ["runtime_exists"]  # Depends on docker/podman existing

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable for container runtime commands."""
        return self.enabled and self._is_container_command(context.command_args)

    def _is_container_command(self, command_args: list[str]) -> bool:
        """Check if command is a container runtime command."""
        if not command_args:
            return False

        first_cmd = command_args[0]
        if first_cmd in ["docker", "podman"]:
            # Check if it's a run command with an image
            if len(command_args) >= 3 and command_args[1] == "run":
                return True

        return False

    def _extract_image_name(self, command_args: list[str]) -> str | None:
        """Extract container image name from command arguments."""
        if not self._is_container_command(command_args):
            return None

        # Find the image name in docker/podman run command
        # Format: docker/podman run [options] IMAGE [command]
        _ = command_args[0]  # docker or podman
        if len(command_args) < 3 or command_args[1] != "run":
            return None

        # Options that take values
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

        # Skip run and look for the image (skip options that start with -)
        i = 2
        while i < len(command_args):
            arg = command_args[i]

            # Skip options and their values
            if arg.startswith("-"):
                # Check if this option takes a value
                if arg in options_with_values:
                    # Skip the option and its value
                    i += 2
                    continue
                elif "=" in arg:
                    # Option with value in same argument (like --env=VAR=value)
                    i += 1
                    continue
                else:
                    # Option without value (like --rm, -i, etc.)
                    i += 1
                    continue
            else:
                # First non-option argument should be the image
                return arg

        return None

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute container version validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "container_runtime": None,
            "image_name": None,
            "image_tag": None,
            "using_latest": False,
            "available_tags": [],
            "latest_tag": None,
            "tag_check_performed": False,
        }

        # Extract runtime and image
        runtime = context.command_args[0] if context.command_args else None
        image_name = self._extract_image_name(context.command_args)

        data["container_runtime"] = runtime
        data["image_name"] = image_name

        if not image_name:
            errors.append("Could not extract container image name from command")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        debug_log(f"Validating version for image: {image_name}")

        try:
            # Parse image name and tag
            image_parts = self._parse_image_name(image_name)
            data.update(image_parts)

            # Check if using latest tag
            current_tag = data.get("image_tag", "latest")
            data["using_latest"] = current_tag in ["latest", ""]

            if data["using_latest"]:
                debug_log(
                    "Image is using 'latest' tag - this is considered best practice for latest version"
                )
            else:
                debug_log(f"Image is using specific tag: {current_tag}")

                # Try to check available tags (best effort)
                tag_info = await self._check_available_tags(runtime, image_name, data["image_tag"])
                data.update(tag_info)

                if data.get("tag_check_performed", False):
                    if not data.get("using_latest_available", True):
                        warnings.append(
                            f"Image tag '{current_tag}' may not be the latest available version. Consider using 'latest' tag or check for newer versions."
                        )
                else:
                    warnings.append(
                        f"Could not verify if tag '{current_tag}' is the latest available version. Consider using 'latest' tag for automatic updates."
                    )

        except Exception as e:
            debug_log(f"Version validation failed with exception: {str(e)}", "ERROR")
            errors.append(f"Version validation failed: {str(e)}")

        execution_time = time.time() - start_time
        passed = len(errors) == 0

        debug_log(
            f"Version validation completed: passed={passed}, using_latest={data.get('using_latest', False)}"
        )

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    def _parse_image_name(self, image_name: str) -> dict[str, Any]:
        """Parse container image name to extract registry, repository, and tag."""
        result = {
            "image_registry": None,
            "image_repository": None,
            "image_tag": "latest",
            "full_image_name": image_name,
        }

        # Split by tag separator
        if ":" in image_name:
            image_part, tag_part = image_name.rsplit(":", 1)
            result["image_tag"] = tag_part
        else:
            image_part = image_name
            result["image_tag"] = "latest"

        # Split registry and repository
        if "/" in image_part:
            parts = image_part.split("/")
            if "." in parts[0] or ":" in parts[0]:  # Likely a registry
                result["image_registry"] = parts[0]
                result["image_repository"] = "/".join(parts[1:])
            else:
                result["image_repository"] = image_part
        else:
            result["image_repository"] = image_part

        debug_log(
            f"Parsed image: registry={result['image_registry']}, repo={result['image_repository']}, tag={result['image_tag']}"
        )
        return result

    async def _check_available_tags(
        self, runtime: str, image_name: str, current_tag: str
    ) -> dict[str, Any]:
        """Check available tags for the image (best effort)."""
        result = {
            "tag_check_performed": False,
            "available_tags": [],
            "latest_tag": None,
            "using_latest_available": True,
            "tag_check_error": None,
        }

        try:
            debug_log(f"Attempting to check available tags for: {image_name}")

            # Note: This is a simplified approach. In practice, you might want to:
            # 1. Use registry API calls for more accurate tag information
            # 2. Implement specific logic for different registries (Docker Hub, Quay, etc.)
            # 3. Handle authentication for private registries

            # For now, we'll do a basic check by trying to pull some common "latest" variants
            latest_variants = ["latest", "stable", "current"]

            for variant in latest_variants:
                if variant == current_tag:
                    result["using_latest_available"] = True
                    result["latest_tag"] = variant
                    break

                # Try to check if variant exists (this is a simplified approach)
                try:
                    variant_image = f"{image_name.split(':')[0]}:{variant}"
                    debug_log(f"Checking if tag exists: {variant}")

                    # Use a quick manifest check (timeout quickly)
                    check_process = await asyncio.create_subprocess_exec(
                        runtime,
                        "manifest",
                        "inspect",
                        variant_image,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )

                    await asyncio.wait_for(check_process.communicate(), timeout=10.0)

                    if check_process.returncode == 0:
                        result["available_tags"].append(variant)
                        if not result["latest_tag"]:
                            result["latest_tag"] = variant
                        debug_log(f"Found available tag: {variant}")

                except (asyncio.TimeoutError, Exception):
                    # Ignore errors for individual tag checks
                    continue

            result["tag_check_performed"] = True

            # If we found any tags and current tag is not among them, suggest update
            if result["available_tags"] and current_tag not in result["available_tags"]:
                result["using_latest_available"] = False

        except Exception as e:
            result["tag_check_error"] = str(e)
            debug_log(f"Tag availability check failed: {str(e)}", "ERROR")

        return result
