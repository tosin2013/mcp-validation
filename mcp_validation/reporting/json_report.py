"""JSON reporting for MCP validation results."""

import datetime
import json
from typing import Any, Dict, List, Optional

from ..core.result import ValidationSession, ValidatorResult


class JSONReporter:
    """Generates JSON reports from validation sessions."""

    def generate_report(
        self,
        session: ValidationSession,
        command_args: List[str],
        env_vars: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Generate a comprehensive JSON report."""

        # Extract aggregated data
        server_info = {}
        capabilities = {}
        tools = []
        prompts = []
        resources = []
        ping_result = None
        error_compliance = None
        security_analysis = {}
        repo_availability = None
        license_validation = None
        runtime_exists = None
        runtime_executable = None

        for result in session.validator_results:
            if result.validator_name == "protocol":
                server_info = result.data.get("server_info", {})
                capabilities = result.data.get("capabilities", {})
            elif result.validator_name == "capabilities":
                tools = result.data.get("tools", [])
                prompts = result.data.get("prompts", [])
                resources = result.data.get("resources", [])
            elif result.validator_name == "ping":
                ping_result = result.data
            elif result.validator_name == "errors":
                error_compliance = result.data
            elif result.validator_name == "security":
                security_analysis = result.data
            elif result.validator_name == "repo_availability":
                repo_availability = result.data
            elif result.validator_name == "license":
                license_validation = result.data
            elif result.validator_name == "runtime_exists":
                runtime_exists = result.data
            elif result.validator_name == "runtime_executable":
                runtime_executable = result.data

        # Build comprehensive report
        report = {
            "report_metadata": {
                "generated_at": datetime.datetime.now().isoformat(),
                "validator_version": "2.0.0",
                "profile_used": session.profile_name,
                "command": " ".join(command_args),
                "environment_variables": env_vars or {},
            },
            "validation_summary": {
                "overall_success": session.overall_success,
                "execution_time_seconds": session.execution_time,
                "total_errors": len(session.errors),
                "total_warnings": len(session.warnings),
                "validators_run": len(session.validator_results),
                "validators_passed": sum(1 for r in session.validator_results if r.passed),
            },
            "server_information": {
                "server_info": server_info,
                "capabilities": capabilities,
                "discovered_items": {
                    "tools": {"count": len(tools), "names": tools},
                    "prompts": {"count": len(prompts), "names": prompts},
                    "resources": {"count": len(resources), "names": resources},
                },
            },
            "validator_results": [
                self._format_validator_result(result) for result in session.validator_results
            ],
            "optional_features": {
                "ping_protocol": {
                    "tested": ping_result is not None,
                    "supported": ping_result.get("supported", False) if ping_result else False,
                    "response_time_ms": (
                        ping_result.get("response_time_ms") if ping_result else None
                    ),
                    "error": ping_result.get("error") if ping_result else None,
                },
                "error_compliance": {
                    "tested": error_compliance is not None,
                    "invalid_method_test": (
                        error_compliance.get("invalid_method_test", {}) if error_compliance else {}
                    ),
                    "malformed_request_test": (
                        error_compliance.get("malformed_request_test", {})
                        if error_compliance
                        else {}
                    ),
                    "compliance_issues": (
                        error_compliance.get("compliance_issues", []) if error_compliance else []
                    ),
                },
            },
            "security_analysis": {
                "executed": bool(security_analysis),
                "tools_scanned": security_analysis.get("tools_scanned", 0),
                "vulnerabilities_found": security_analysis.get("vulnerabilities_found", 0),
                "vulnerability_types": security_analysis.get("vulnerability_types", []),
                "risk_levels": security_analysis.get("risk_levels", []),
                "issues_found": security_analysis.get("issues_found", 0),
                "issue_codes": security_analysis.get("issue_codes", []),
                "issues": security_analysis.get("issues", []),
                "scan_file": security_analysis.get("scan_file"),
            },
            "repository_validation": {
                "repo_availability": {
                    "executed": repo_availability is not None,
                    "repo_url": repo_availability.get("repo_url") if repo_availability else None,
                    "is_git_repo": (
                        repo_availability.get("is_git_repo", False) if repo_availability else False
                    ),
                    "clone_successful": (
                        repo_availability.get("clone_successful", False)
                        if repo_availability
                        else False
                    ),
                    "has_readme": (
                        repo_availability.get("has_readme", False) if repo_availability else False
                    ),
                    "has_license": (
                        repo_availability.get("has_license", False) if repo_availability else False
                    ),
                    "readme_files": (
                        repo_availability.get("readme_files", []) if repo_availability else []
                    ),
                    "license_files": (
                        repo_availability.get("license_files", []) if repo_availability else []
                    ),
                },
                "license_validation": {
                    "executed": license_validation is not None,
                    "license_detected": (
                        license_validation.get("license_detected", False)
                        if license_validation
                        else False
                    ),
                    "license_type": (
                        license_validation.get("license_type") if license_validation else None
                    ),
                    "license_acceptable": (
                        license_validation.get("license_acceptable", False)
                        if license_validation
                        else False
                    ),
                    "license_files_found": (
                        license_validation.get("license_files_found", [])
                        if license_validation
                        else []
                    ),
                },
            },
            "runtime_validation": {
                "runtime_exists": {
                    "executed": runtime_exists is not None,
                    "runtime_command": (
                        runtime_exists.get("runtime_command") if runtime_exists else None
                    ),
                    "runtime_found": (
                        runtime_exists.get("runtime_found", False) if runtime_exists else False
                    ),
                    "runtime_path": runtime_exists.get("runtime_path") if runtime_exists else None,
                    "runtime_version": (
                        runtime_exists.get("runtime_version") if runtime_exists else None
                    ),
                    "path_locations": (
                        runtime_exists.get("path_locations", []) if runtime_exists else []
                    ),
                },
                "runtime_executable": {
                    "executed": runtime_executable is not None,
                    "executable_check_passed": (
                        runtime_executable.get("executable_check_passed", False)
                        if runtime_executable
                        else False
                    ),
                    "test_execution_successful": (
                        runtime_executable.get("test_execution_successful", False)
                        if runtime_executable
                        else False
                    ),
                    "test_command_used": (
                        runtime_executable.get("test_command_used") if runtime_executable else None
                    ),
                    "test_execution_time": (
                        runtime_executable.get("test_execution_time", 0)
                        if runtime_executable
                        else 0
                    ),
                    "test_exit_code": (
                        runtime_executable.get("test_exit_code") if runtime_executable else None
                    ),
                },
            },
            "issues": {"errors": session.errors, "warnings": session.warnings},
        }

        return report

    def _format_validator_result(self, result: ValidatorResult) -> Dict[str, Any]:
        """Format a single validator result for JSON output."""
        return {
            "validator_name": result.validator_name,
            "passed": result.passed,
            "execution_time_seconds": result.execution_time,
            "error_count": len(result.errors),
            "warning_count": len(result.warnings),
            "errors": result.errors,
            "warnings": result.warnings,
            "data": result.data,
        }

    def save_report(
        self,
        session: ValidationSession,
        filename: str,
        command_args: List[str],
        env_vars: Optional[Dict[str, str]] = None,
    ) -> None:
        """Generate and save JSON report to file."""
        report = self.generate_report(session, command_args, env_vars)

        with open(filename, "w") as f:
            json.dump(report, f, indent=2, default=str)

        print(f"📋 JSON report saved to: {filename}")
