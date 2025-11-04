"""Package registry validator for MCP validation."""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from ..utils.debug import debug_log as _debug_log
from .base import BaseValidator, ValidationContext, ValidatorResult


def debug_log(message: str, level: str = "INFO") -> None:
    """Registry-specific debug logging wrapper."""
    _debug_log(message, level, "REGISTRY")


@dataclass
class PackageInfo:
    """Information about a package to validate."""

    name: str
    version: str | None = None
    registry_type: str = "npm"  # npm, pypi, docker


def extract_packages_from_command(command_args: list[str]) -> list[PackageInfo]:
    """Extract package information from MCP command arguments."""
    packages = []

    if not command_args:
        debug_log("No command arguments provided", "INFO", "REGISTRY")
        return packages

    # Join command args to get full command
    full_command = " ".join(command_args)
    debug_log(f"Extracting packages from command: {full_command}")

    # Pattern 1: npx package@version (including scoped packages)
    npx_pattern = r"npx\s+(?:-[gy]\s+|--[^\s]+\s+)*(@?[^@\s]+)(?:@([^\s]+))?"
    npx_matches = re.findall(npx_pattern, full_command)

    for package_name, version in npx_matches:
        # Clean up package name (remove empty strings from regex groups)
        package_name = package_name.strip()
        version = version.strip() if version else None

        if package_name and not package_name.startswith("-"):
            package_info = PackageInfo(name=package_name, version=version, registry_type="npm")
            packages.append(package_info)
            debug_log(f"Extracted npm package: {package_name}" + (f"@{version}" if version else ""))

    # Pattern 2: python -m package or python package.py
    python_patterns = [
        r"python3?\s+-m\s+([^\s]+)",  # python -m package
        r"python3?\s+([^\s]+\.py)",  # python script.py
    ]

    for pattern in python_patterns:
        python_matches = re.findall(pattern, full_command)
        for match in python_matches:
            if match and not match.startswith("-"):
                # Extract potential package name (convert to PyPI naming)
                package_name = match.replace(".py", "").replace("_", "-")
                package_info = PackageInfo(name=package_name, registry_type="pypi")
                packages.append(package_info)
                debug_log(f"Extracted python package: {package_name}")

    # Pattern 3: docker run image:tag
    docker_pattern = r"docker\s+run\s+(?:[^\s]+\s+)*([^\s:]+)(?::([^\s]+))?"
    docker_matches = re.findall(docker_pattern, full_command)

    for image_name, tag in docker_matches:
        image_name = image_name.strip()
        tag = tag.strip() if tag else None

        if image_name and not image_name.startswith("-"):
            package_info = PackageInfo(name=image_name, version=tag, registry_type="docker")
            packages.append(package_info)
            debug_log(f"Extracted docker image: {image_name}" + (f":{tag}" if tag else ""))

    debug_log(f"Total packages extracted from command: {len(packages)}")
    return packages


class RegistryChecker(Protocol):
    """Protocol for registry checker implementations."""

    async def check_package(
        self, package: PackageInfo, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Check if a package exists in the registry."""
        ...


class NPMRegistryChecker:
    """NPM registry checker implementation."""

    def __init__(self, registry_url: str = "https://registry.npmjs.org"):
        self.registry_url = registry_url.rstrip("/")

    async def check_package(
        self, package: PackageInfo, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Check if NPM package exists."""
        url = f"{self.registry_url}/{package.name}"
        debug_log(f"NPM: Checking package '{package.name}' at {url}")

        try:
            debug_log(f"NPM: Making HTTP request to {url}")
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                debug_log(f"NPM: Response status: {response.status}")
                if response.status == 200:
                    data = await response.json()
                    versions = list(data.get("versions", {}).keys())
                    debug_log(
                        f"NPM: Package '{package.name}' exists, found {len(versions)} versions"
                    )

                    result = {
                        "exists": True,
                        "name": package.name,
                        "registry_url": self.registry_url,
                        "available_versions": versions[:10],  # Limit to first 10 versions
                        "latest_version": data.get("dist-tags", {}).get("latest"),
                        "description": data.get("description", ""),
                    }

                    if package.version:
                        version_exists = package.version in data.get("versions", {})
                        debug_log(f"NPM: Version '{package.version}' exists: {version_exists}")
                        result["requested_version_exists"] = version_exists

                    return result
                elif response.status == 404:
                    debug_log(f"NPM: Package '{package.name}' not found (404)")
                    return {
                        "exists": False,
                        "name": package.name,
                        "registry_url": self.registry_url,
                        "error": "Package not found",
                    }
                else:
                    debug_log(f"NPM: Registry error: HTTP {response.status}", "WARN")
                    return {
                        "exists": False,
                        "name": package.name,
                        "registry_url": self.registry_url,
                        "error": f"Registry error: HTTP {response.status}",
                    }
        except asyncio.TimeoutError:
            debug_log(f"NPM: Request timeout for package '{package.name}'", "ERROR")
            return {
                "exists": False,
                "name": package.name,
                "registry_url": self.registry_url,
                "error": "Registry request timeout",
            }
        except Exception as e:
            debug_log(f"NPM: Request failed for package '{package.name}': {str(e)}", "ERROR")
            return {
                "exists": False,
                "name": package.name,
                "registry_url": self.registry_url,
                "error": f"Registry request failed: {str(e)}",
            }


class PyPIRegistryChecker:
    """PyPI registry checker implementation."""

    def __init__(self, registry_url: str = "https://pypi.org"):
        self.registry_url = registry_url.rstrip("/")

    async def check_package(
        self, package: PackageInfo, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Check if PyPI package exists."""
        url = f"{self.registry_url}/pypi/{package.name}/json"
        debug_log(f"PyPI: Checking package '{package.name}' at {url}")

        try:
            debug_log(f"PyPI: Making HTTP request to {url}")
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                debug_log(f"PyPI: Response status: {response.status}")
                if response.status == 200:
                    data = await response.json()
                    versions = list(data.get("releases", {}).keys())
                    debug_log(
                        f"PyPI: Package '{package.name}' exists, found {len(versions)} versions"
                    )

                    result = {
                        "exists": True,
                        "name": package.name,
                        "registry_url": self.registry_url,
                        "available_versions": versions[-10:],  # Last 10 versions
                        "latest_version": data.get("info", {}).get("version"),
                        "description": data.get("info", {}).get("summary", ""),
                    }

                    if package.version:
                        version_exists = package.version in data.get("releases", {})
                        debug_log(f"PyPI: Version '{package.version}' exists: {version_exists}")
                        result["requested_version_exists"] = version_exists

                    return result
                elif response.status == 404:
                    debug_log(f"PyPI: Package '{package.name}' not found (404)")
                    return {
                        "exists": False,
                        "name": package.name,
                        "registry_url": self.registry_url,
                        "error": "Package not found",
                    }
                else:
                    debug_log(f"PyPI: Registry error: HTTP {response.status}", "WARN")
                    return {
                        "exists": False,
                        "name": package.name,
                        "registry_url": self.registry_url,
                        "error": f"Registry error: HTTP {response.status}",
                    }
        except asyncio.TimeoutError:
            debug_log(f"PyPI: Request timeout for package '{package.name}'", "ERROR")
            return {
                "exists": False,
                "name": package.name,
                "registry_url": self.registry_url,
                "error": "Registry request timeout",
            }
        except Exception as e:
            debug_log(f"PyPI: Request failed for package '{package.name}': {str(e)}", "ERROR")
            return {
                "exists": False,
                "name": package.name,
                "registry_url": self.registry_url,
                "error": f"Registry request failed: {str(e)}",
            }


class DockerRegistryChecker:
    """Docker Hub registry checker implementation."""

    def __init__(self, registry_url: str = "https://registry-1.docker.io"):
        self.registry_url = registry_url.rstrip("/")
        self.hub_url = "https://hub.docker.com"

    async def check_package(
        self, package: PackageInfo, session: aiohttp.ClientSession
    ) -> dict[str, Any]:
        """Check if Docker image exists."""
        # For Docker Hub, use the Hub API which is more accessible
        # Format: namespace/repository or just repository for official images
        if "/" not in package.name:
            # Official image
            url = f"{self.hub_url}/v2/repositories/library/{package.name}"
            debug_log(f"Docker: Checking official image '{package.name}' at {url}")
        else:
            # User/org image
            url = f"{self.hub_url}/v2/repositories/{package.name}"
            debug_log(f"Docker: Checking user/org image '{package.name}' at {url}")

        try:
            debug_log(f"Docker: Making HTTP request to {url}")
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                debug_log(f"Docker: Response status: {response.status}")
                if response.status == 200:
                    data = await response.json()
                    debug_log(f"Docker: Image '{package.name}' exists")

                    # Get tags
                    tags_url = f"{url}/tags"
                    debug_log(f"Docker: Getting tags from {tags_url}")
                    async with session.get(
                        tags_url, timeout=aiohttp.ClientTimeout(total=10)
                    ) as tags_response:
                        tags_data = (
                            await tags_response.json() if tags_response.status == 200 else {}
                        )
                        tags = [tag["name"] for tag in tags_data.get("results", [])[:10]]
                        debug_log(f"Docker: Found {len(tags)} tags for '{package.name}'")

                    result = {
                        "exists": True,
                        "name": package.name,
                        "registry_url": self.hub_url,
                        "available_tags": tags,
                        "description": data.get("description", ""),
                        "is_official": data.get("is_official", False),
                        "pull_count": data.get("pull_count", 0),
                    }

                    if package.version:
                        tag_exists = package.version in tags
                        debug_log(f"Docker: Tag '{package.version}' exists: {tag_exists}")
                        result["requested_tag_exists"] = tag_exists

                    return result
                elif response.status == 404:
                    debug_log(f"Docker: Image '{package.name}' not found (404)")
                    return {
                        "exists": False,
                        "name": package.name,
                        "registry_url": self.hub_url,
                        "error": "Image not found",
                    }
                else:
                    debug_log(f"Docker: Registry error: HTTP {response.status}", "WARN")
                    return {
                        "exists": False,
                        "name": package.name,
                        "registry_url": self.hub_url,
                        "error": f"Registry error: HTTP {response.status}",
                    }
        except asyncio.TimeoutError:
            debug_log(f"Docker: Request timeout for image '{package.name}'", "ERROR")
            return {
                "exists": False,
                "name": package.name,
                "registry_url": self.hub_url,
                "error": "Registry request timeout",
            }
        except Exception as e:
            debug_log(f"Docker: Request failed for image '{package.name}': {str(e)}", "ERROR")
            return {
                "exists": False,
                "name": package.name,
                "registry_url": self.hub_url,
                "error": f"Registry request failed: {str(e)}",
            }


class RegistryValidator(BaseValidator):
    """Validator for checking package existence in registries."""

    def __init__(self, config: dict[str, Any] = None):
        super().__init__(config)
        debug_log(f"Initializing RegistryValidator with config: {config}")

        # Initialize registry checkers with custom URLs if provided
        registry_configs = self.config.get("registries", {})
        debug_log(f"Registry configurations: {registry_configs}")

        self.checkers = {
            "npm": NPMRegistryChecker(
                registry_configs.get("npm_url", "https://registry.npmjs.org")
            ),
            "pypi": PyPIRegistryChecker(registry_configs.get("pypi_url", "https://pypi.org")),
            "docker": DockerRegistryChecker(
                registry_configs.get("docker_url", "https://registry-1.docker.io")
            ),
        }

        # Packages to check - can be configured via config
        self.packages = self._parse_packages_config()
        debug_log(f"Parsed {len(self.packages)} packages for validation")

    def _parse_packages_config(self) -> list[PackageInfo]:
        """Parse packages configuration from validator config."""
        packages = []
        packages_config = self.config.get("packages", [])
        debug_log(f"Parsing packages config: {packages_config}")

        for pkg_config in packages_config:
            debug_log(f"Processing package config: {pkg_config}")
            if isinstance(pkg_config, str):
                # Simple string format: "package_name" or "package_name@version"
                if "@" in pkg_config and not pkg_config.startswith("@"):
                    name, version = pkg_config.split("@", 1)
                else:
                    name, version = pkg_config, None

                # Infer registry type from name patterns
                if name.startswith("docker:"):
                    registry_type = "docker"
                    name = name.replace("docker:", "")
                elif name.startswith("pypi:") or name.endswith(".py"):
                    # Only classify as PyPI if explicitly prefixed or ends with .py
                    registry_type = "pypi"
                    name = name.replace("pypi:", "")
                elif "/" in name and not name.startswith("@"):
                    # Docker images often have '/' but npm scoped packages start with '@'
                    registry_type = "docker"
                else:
                    # Default to npm for ambiguous names (most common case)
                    registry_type = "npm"

                debug_log(
                    f"Parsed string package: {name} (type: {registry_type}, version: {version})"
                )
                packages.append(
                    PackageInfo(name=name, version=version, registry_type=registry_type)
                )

            elif isinstance(pkg_config, dict):
                # Detailed configuration
                package_info = PackageInfo(
                    name=pkg_config["name"],
                    version=pkg_config.get("version"),
                    registry_type=pkg_config.get("type", "npm"),
                )
                debug_log(
                    f"Parsed dict package: {package_info.name} (type: {package_info.registry_type}, version: {package_info.version})"
                )
                packages.append(package_info)

        debug_log(f"Total packages parsed: {len(packages)}")
        return packages

    @property
    def name(self) -> str:
        return "registry"

    @property
    def description(self) -> str:
        return "Validates that specified packages exist in their respective registries"

    @property
    def dependencies(self) -> list[str]:
        return []  # No dependencies on other validators

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute registry validation."""
        start_time = time.time()
        errors = []
        warnings = []

        # Extract packages from MCP command if available
        command_packages = []
        if hasattr(context, "command_args") and context.command_args:
            debug_log("Extracting packages from MCP command")
            command_packages = extract_packages_from_command(context.command_args)

        # Use command packages if available, otherwise fall back to configured packages
        packages_to_validate = command_packages if command_packages else self.packages

        debug_log(f"Starting registry validation with {len(packages_to_validate)} packages")
        debug_log(f"Package source: {'command' if command_packages else 'configuration'}")

        data = {
            "packages_checked": [],
            "total_packages": len(packages_to_validate),
            "packages_found": 0,
            "packages_missing": 0,
            "registry_errors": 0,
            "package_source": "command" if command_packages else "configuration",
        }

        if not packages_to_validate:
            debug_log("No packages to validate, returning early with warning")
            warnings.append("No packages found in command or configuration for registry validation")
            return ValidatorResult(
                validator_name=self.name,
                passed=True,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        debug_log("Creating HTTP session for registry requests")
        async with aiohttp.ClientSession() as session:
            for i, package in enumerate(packages_to_validate, 1):
                debug_log(
                    f"Processing package {i}/{len(packages_to_validate)}: {package.name} ({package.registry_type})"
                )
                checker = self.checkers.get(package.registry_type)
                if not checker:
                    debug_log(f"Unsupported registry type: {package.registry_type}", "ERROR")
                    errors.append(
                        f"Unsupported registry type: {package.registry_type} for package {package.name}"
                    )
                    data["registry_errors"] += 1
                    continue

                debug_log(f"Checking package {package.name} with {package.registry_type} checker")
                result = await checker.check_package(package, session)
                debug_log(
                    f"Check result for {package.name}: exists={result.get('exists')}, error={result.get('error')}"
                )
                data["packages_checked"].append(result)

                if result.get("exists", False):
                    debug_log(f"Package {package.name} exists")
                    data["packages_found"] += 1

                    # Check specific version if requested
                    if package.version:
                        version_key = (
                            "requested_version_exists"
                            if package.registry_type != "docker"
                            else "requested_tag_exists"
                        )
                        version_exists = result.get(version_key, True)
                        debug_log(
                            f"Version check for {package.name}@{package.version}: {version_exists}"
                        )
                        if not version_exists:
                            warning_msg = f"Package {package.name} exists but version/tag {package.version} not found"
                            debug_log(f"Adding warning: {warning_msg}", "WARN")
                            warnings.append(warning_msg)

                elif result.get("error"):
                    # Network or registry errors - treat as warnings for transient issues
                    error_msg = result.get("error", "")
                    debug_log(f"Error for package {package.name}: {error_msg}")
                    if "not found" in error_msg.lower() or "404" in error_msg:
                        # Definitely missing package
                        error_text = (
                            f"Package {package.name} not found in {package.registry_type} registry"
                        )
                        debug_log(f"Adding error (missing package): {error_text}", "ERROR")
                        errors.append(error_text)
                        data["packages_missing"] += 1
                    else:
                        # Network or other errors
                        warning_text = f"Could not verify package {package.name}: {error_msg}"
                        debug_log(f"Adding warning (network error): {warning_text}", "WARN")
                        warnings.append(warning_text)
                        data["registry_errors"] += 1

                else:
                    # Package definitely doesn't exist
                    error_text = (
                        f"Package {package.name} not found in {package.registry_type} registry"
                    )
                    debug_log(f"Adding error (no exists flag): {error_text}", "ERROR")
                    errors.append(error_text)
                    data["packages_missing"] += 1

        # Validation passes if all required packages exist (no errors)
        passed = len(errors) == 0
        execution_time = time.time() - start_time

        debug_log("Registry validation completed:")
        debug_log(f"  - Passed: {passed}")
        debug_log(f"  - Errors: {len(errors)}")
        debug_log(f"  - Warnings: {len(warnings)}")
        debug_log(f"  - Packages found: {data['packages_found']}/{data['total_packages']}")
        debug_log(f"  - Execution time: {execution_time:.2f}s")

        if errors:
            debug_log("Error details:")
            for error in errors:
                debug_log(f"  - {error}")

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    def is_applicable(self, context: ValidationContext) -> bool:
        """Registry validation is applicable when packages are configured and enabled."""
        return self.enabled and len(self.packages) > 0
