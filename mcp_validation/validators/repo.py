"""Repository validation for MCP servers."""

import asyncio
import os
import re
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..utils.debug import debug_log as _debug_log
from .base import BaseValidator, ValidationContext, ValidatorResult


def debug_log(message: str, level: str = "INFO") -> None:
    """Repository-specific debug logging wrapper."""
    _debug_log(message, level, "REPO")


class RepoAvailabilityValidator(BaseValidator):
    """Validates that a repository URL is accessible and contains OSS project files."""

    @property
    def name(self) -> str:
        return "repo_availability"

    @property
    def description(self) -> str:
        return "Validates repository URL accessibility and OSS project structure"

    @property
    def dependencies(self) -> List[str]:
        return []  # No dependencies - runs first

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable if repo_url is provided in config."""
        repo_url = self.config.get("repo_url")
        return self.enabled and repo_url is not None

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute repository availability validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "repo_url": None,
            "is_git_repo": False,
            "clone_successful": False,
            "has_readme": False,
            "has_license": False,
            "readme_files": [],
            "license_files": [],
            "repo_structure": {},
        }

        repo_url = self.config.get("repo_url")
        if not repo_url:
            errors.append("Repository URL not provided")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        data["repo_url"] = repo_url
        debug_log(f"Validating repository URL: {repo_url}")

        # Validate URL format
        if not self._is_valid_repo_url(repo_url):
            errors.append(f"Invalid repository URL format: {repo_url}")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        # Check if git is available
        if not shutil.which("git"):
            errors.append("Git command not found - cannot clone repository")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        # Test repository cloning
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="mcp_repo_validation_")
            debug_log(f"Created temporary directory: {temp_dir}")

            # Attempt to clone the repository
            clone_result = await self._clone_repository(repo_url, temp_dir)
            data.update(clone_result)

            if not clone_result["clone_successful"]:
                errors.append(
                    f"Failed to clone repository: {clone_result.get('error', 'Unknown error')}"
                )
            else:
                debug_log("Repository cloned successfully")
                data["is_git_repo"] = True

                # Check for required files
                repo_path = os.path.join(temp_dir, "repo")
                file_check_result = self._check_required_files(repo_path)
                data.update(file_check_result)

                # Validate OSS project structure
                if not data["has_readme"]:
                    errors.append("Repository missing README file")
                if not data["has_license"]:
                    errors.append("Repository missing LICENSE file")

                # Add warnings for missing common files
                if not data["readme_files"]:
                    warnings.append("No README files found in repository root")
                if not data["license_files"]:
                    warnings.append("No LICENSE files found in repository root")

        except Exception as e:
            debug_log(f"Repository validation failed with exception: {str(e)}", "ERROR")
            errors.append(f"Repository validation failed: {str(e)}")

        finally:
            # Clean up temporary directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    debug_log(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    debug_log(f"Failed to clean up temporary directory: {str(e)}", "WARN")

        execution_time = time.time() - start_time
        passed = len(errors) == 0

        debug_log(
            f"Repository validation completed: passed={passed}, errors={len(errors)}, warnings={len(warnings)}"
        )

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    def _is_valid_repo_url(self, url: str) -> bool:
        """Check if URL looks like a valid repository URL."""
        try:
            parsed = urlparse(url)

            # Must have scheme and netloc
            if not parsed.scheme or not parsed.netloc:
                return False

            # Support common git hosting services
            valid_hosts = [
                "github.com",
                "gitlab.com",
                "bitbucket.org",
                "git.sr.ht",
                "codeberg.org",
                "gitea.com",
            ]

            # Allow any host that looks reasonable
            host_valid = any(host in parsed.netloc for host in valid_hosts) or re.match(
                r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", parsed.netloc
            )

            if not host_valid:
                return False

            # Check for git-like path patterns
            path = parsed.path.lower()
            if (
                path.endswith(".git")
                or "/git/" in path
                or any(service in parsed.netloc for service in valid_hosts)
            ):
                return True

            # Allow paths that look like repo paths
            return bool(re.match(r"^/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/?$", parsed.path))

        except Exception:
            return False

    async def _clone_repository(self, repo_url: str, temp_dir: str) -> Dict[str, Any]:
        """Attempt to clone the repository."""
        result = {
            "clone_successful": False,
            "error": None,
            "clone_time_seconds": 0,
        }

        clone_path = os.path.join(temp_dir, "repo")
        timeout = self.config.get("clone_timeout", 30.0)

        try:
            debug_log(f"Attempting to clone repository with timeout {timeout}s")
            start_time = time.time()

            # Use git clone with shallow clone for faster operation
            process = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                repo_url,
                clone_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            clone_time = time.time() - start_time
            result["clone_time_seconds"] = round(clone_time, 2)

            if process.returncode == 0:
                result["clone_successful"] = True
                debug_log(f"Repository cloned successfully in {clone_time:.2f}s")
            else:
                error_msg = stderr.decode().strip() if stderr else "Unknown clone error"
                result["error"] = error_msg
                debug_log(f"Git clone failed: {error_msg}", "ERROR")

        except asyncio.TimeoutError:
            result["error"] = f"Clone operation timed out after {timeout} seconds"
            debug_log(f"Clone operation timed out after {timeout}s", "ERROR")
        except Exception as e:
            result["error"] = f"Clone operation failed: {str(e)}"
            debug_log(f"Clone operation failed with exception: {str(e)}", "ERROR")

        return result

    def _check_required_files(self, repo_path: str) -> Dict[str, Any]:
        """Check for required OSS project files."""
        result = {
            "has_readme": False,
            "has_license": False,
            "readme_files": [],
            "license_files": [],
            "repo_structure": {},
        }

        if not os.path.exists(repo_path):
            debug_log(f"Repository path does not exist: {repo_path}", "ERROR")
            return result

        try:
            # Get directory listing
            files = os.listdir(repo_path)
            result["repo_structure"] = {"total_files": len(files), "directories": [], "files": []}

            debug_log(f"Repository contains {len(files)} items")

            # Categorize files and directories
            for item in files:
                item_path = os.path.join(repo_path, item)
                if os.path.isdir(item_path):
                    result["repo_structure"]["directories"].append(item)
                else:
                    result["repo_structure"]["files"].append(item)

            # Check for README files
            readme_patterns = [
                r"^readme$",
                r"^readme\.md$",
                r"^readme\.txt$",
                r"^readme\.rst$",
                r"^read\.me$",
                r"^readme\.markdown$",
            ]

            for file in files:
                if any(re.match(pattern, file.lower()) for pattern in readme_patterns):
                    result["readme_files"].append(file)
                    result["has_readme"] = True
                    debug_log(f"Found README file: {file}")

            # Check for LICENSE files
            license_patterns = [
                r"^license$",
                r"^license\.md$",
                r"^license\.txt$",
                r"^licence$",
                r"^licence\.md$",
                r"^licence\.txt$",
                r"^copying$",
                r"^copying\.txt$",
                r"^copyright$",
            ]

            for file in files:
                if any(re.match(pattern, file.lower()) for pattern in license_patterns):
                    result["license_files"].append(file)
                    result["has_license"] = True
                    debug_log(f"Found LICENSE file: {file}")

            debug_log(
                f"File check completed: README={result['has_readme']}, LICENSE={result['has_license']}"
            )

        except Exception as e:
            debug_log(f"Error checking repository files: {str(e)}", "ERROR")

        return result


class LicenseValidator(BaseValidator):
    """Validates that the repository has an acceptable OSS license."""

    # Acceptable OSS licenses for Red Hat
    ACCEPTABLE_LICENSES = {
        "apache-2.0": ["apache license 2.0", "apache license version 2.0", "apache-2.0"],
        "mit": ["mit license", "mit"],
        "gpl-2.0": ["gnu general public license version 2", "gpl-2.0", "gplv2"],
        "gpl-3.0": ["gnu general public license version 3", "gpl-3.0", "gplv3"],
        "lgpl-2.1": ["gnu lesser general public license version 2.1", "lgpl-2.1", "lgplv2.1"],
        "lgpl-3.0": ["gnu lesser general public license version 3", "lgpl-3.0", "lgplv3"],
        "bsd-2-clause": ["bsd 2-clause license", "bsd-2-clause", "simplified bsd"],
        "bsd-3-clause": ["bsd 3-clause license", "bsd-3-clause", "new bsd"],
        "mpl-2.0": ["mozilla public license 2.0", "mpl-2.0"],
    }

    @property
    def name(self) -> str:
        return "license"

    @property
    def description(self) -> str:
        return "Validates repository has acceptable OSS license"

    @property
    def dependencies(self) -> List[str]:
        return ["repo_availability"]  # Depends on repo being available

    def is_applicable(self, context: ValidationContext) -> bool:
        """Only applicable if repo_url is provided and repo is available."""
        repo_url = self.config.get("repo_url")
        return self.enabled and repo_url is not None

    async def validate(self, context: ValidationContext) -> ValidatorResult:
        """Execute license validation."""
        start_time = time.time()
        errors = []
        warnings = []
        data = {
            "repo_url": self.config.get("repo_url"),
            "license_detected": False,
            "license_type": None,
            "license_acceptable": False,
            "license_content_preview": None,
            "license_files_found": [],
        }

        repo_url = self.config.get("repo_url")
        if not repo_url:
            errors.append("Repository URL not provided")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        debug_log(f"Validating license for repository: {repo_url}")

        # Check if git is available
        if not shutil.which("git"):
            errors.append("Git command not found - cannot clone repository")
            return ValidatorResult(
                validator_name=self.name,
                passed=False,
                errors=errors,
                warnings=warnings,
                data=data,
                execution_time=time.time() - start_time,
            )

        # Clone repository and check license
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix="mcp_license_validation_")
            debug_log(f"Created temporary directory: {temp_dir}")

            # Clone repository
            clone_path = os.path.join(temp_dir, "repo")
            clone_result = await self._clone_repository(repo_url, clone_path)

            if not clone_result["clone_successful"]:
                errors.append(
                    f"Failed to clone repository: {clone_result.get('error', 'Unknown error')}"
                )
            else:
                # Check license files
                license_result = await self._check_license(clone_path)
                data.update(license_result)

                if not data["license_detected"]:
                    errors.append("No license file found in repository")
                elif not data["license_acceptable"]:
                    detected_type = data.get("license_type", "unknown")
                    errors.append(f"License '{detected_type}' is not acceptable for OSS projects")
                    warnings.append(
                        f"Acceptable licenses: {', '.join(self.ACCEPTABLE_LICENSES.keys())}"
                    )
                else:
                    debug_log(f"Acceptable license detected: {data['license_type']}")

        except Exception as e:
            debug_log(f"License validation failed with exception: {str(e)}", "ERROR")
            errors.append(f"License validation failed: {str(e)}")

        finally:
            # Clean up temporary directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    debug_log(f"Cleaned up temporary directory: {temp_dir}")
                except Exception as e:
                    debug_log(f"Failed to clean up temporary directory: {str(e)}", "WARN")

        execution_time = time.time() - start_time
        passed = len(errors) == 0

        debug_log(f"License validation completed: passed={passed}, errors={len(errors)}")

        return ValidatorResult(
            validator_name=self.name,
            passed=passed,
            errors=errors,
            warnings=warnings,
            data=data,
            execution_time=execution_time,
        )

    async def _clone_repository(self, repo_url: str, clone_path: str) -> Dict[str, Any]:
        """Clone repository for license checking."""
        result = {"clone_successful": False, "error": None}
        timeout = self.config.get("clone_timeout", 30.0)

        try:
            debug_log("Cloning repository for license check")
            process = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                "--depth",
                "1",
                repo_url,
                clone_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

            if process.returncode == 0:
                result["clone_successful"] = True
                debug_log("Repository cloned successfully for license check")
            else:
                error_msg = stderr.decode().strip() if stderr else "Unknown clone error"
                result["error"] = error_msg
                debug_log(f"Git clone failed: {error_msg}", "ERROR")

        except asyncio.TimeoutError:
            result["error"] = f"Clone operation timed out after {timeout} seconds"
        except Exception as e:
            result["error"] = f"Clone operation failed: {str(e)}"

        return result

    async def _check_license(self, repo_path: str) -> Dict[str, Any]:
        """Check and validate license files."""
        result = {
            "license_detected": False,
            "license_type": None,
            "license_acceptable": False,
            "license_content_preview": None,
            "license_files_found": [],
        }

        if not os.path.exists(repo_path):
            return result

        try:
            files = os.listdir(repo_path)

            # Find license files
            license_patterns = [
                r"^license$",
                r"^license\.md$",
                r"^license\.txt$",
                r"^licence$",
                r"^licence\.md$",
                r"^licence\.txt$",
                r"^copying$",
                r"^copying\.txt$",
            ]

            license_files = []
            for file in files:
                if any(re.match(pattern, file.lower()) for pattern in license_patterns):
                    license_files.append(file)
                    result["license_files_found"].append(file)

            if not license_files:
                debug_log("No license files found")
                return result

            result["license_detected"] = True
            debug_log(f"Found license files: {license_files}")

            # Read and analyze the first license file
            license_file_path = os.path.join(repo_path, license_files[0])
            try:
                with open(license_file_path, encoding="utf-8", errors="ignore") as f:
                    license_content = f.read().lower()

                # Get preview (first 200 characters)
                result["license_content_preview"] = (
                    license_content[:200] + "..." if len(license_content) > 200 else license_content
                )

                # Detect license type
                detected_license = self._detect_license_type(license_content)
                if detected_license:
                    result["license_type"] = detected_license
                    result["license_acceptable"] = True
                    debug_log(f"Detected acceptable license: {detected_license}")
                else:
                    result["license_type"] = "unknown"
                    result["license_acceptable"] = False
                    debug_log("License type not recognized or not acceptable")

            except Exception as e:
                debug_log(f"Failed to read license file {license_files[0]}: {str(e)}", "ERROR")

        except Exception as e:
            debug_log(f"Error checking license: {str(e)}", "ERROR")

        return result

    def _detect_license_type(self, license_content: str) -> Optional[str]:
        """Detect license type from content."""
        content_lower = license_content.lower()

        for license_key, patterns in self.ACCEPTABLE_LICENSES.items():
            for pattern in patterns:
                if pattern.lower() in content_lower:
                    return license_key

        # Additional pattern matching for common license indicators
        if "apache" in content_lower and "2.0" in content_lower:
            return "apache-2.0"
        elif "mit license" in content_lower or "mit " in content_lower:
            return "mit"
        elif "gnu general public license" in content_lower:
            if "version 3" in content_lower or "v3" in content_lower:
                return "gpl-3.0"
            elif "version 2" in content_lower or "v2" in content_lower:
                return "gpl-2.0"
        elif "gnu lesser general public license" in content_lower:
            if "version 3" in content_lower or "v3" in content_lower:
                return "lgpl-3.0"
            elif "version 2.1" in content_lower or "v2.1" in content_lower:
                return "lgpl-2.1"
        elif "bsd" in content_lower:
            if "3-clause" in content_lower or "three clause" in content_lower:
                return "bsd-3-clause"
            elif "2-clause" in content_lower or "two clause" in content_lower:
                return "bsd-2-clause"
        elif "mozilla public license" in content_lower and "2.0" in content_lower:
            return "mpl-2.0"

        return None
