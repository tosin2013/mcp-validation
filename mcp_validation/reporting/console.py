"""Console reporting for MCP validation results."""

from typing import Any

from ..core.result import ValidationSession, ValidatorResult


class ConsoleReporter:
    """Formats and displays validation results to console."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def report_session(self, session: ValidationSession) -> None:
        """Report a complete validation session to console."""
        print(f"✓ Valid: {session.overall_success}")
        print(f"⏱ Execution time: {session.execution_time:.2f}s")
        print(f"📋 Profile: {session.profile_name}")

        # Report individual validator results
        for result in session.validator_results:
            self._report_validator_result(result)

        # Report overall errors and warnings
        if session.errors:
            print("\n❌ Errors:")
            for error in session.errors:
                print(f"  - {error}")

        if session.warnings and (self.verbose or not session.overall_success):
            print("\n⚠️  Warnings:")
            for warning in session.warnings:
                print(f"  - {warning}")

    def _report_validator_result(self, result: ValidatorResult) -> None:
        """Report results from a single validator."""
        status_icon = "✅" if result.passed else "❌"
        print(
            f"{status_icon} {result.validator_name.title()}: {'Passed' if result.passed else 'Failed'}"
        )

        # Report validator-specific data
        if result.validator_name == "protocol":
            self._report_protocol_data(result.data)
        elif result.validator_name == "capabilities":
            self._report_capabilities_data(result.data)
        elif result.validator_name == "ping":
            self._report_ping_data(result.data)
        elif result.validator_name == "errors":
            self._report_errors_data(result.data)
        elif result.validator_name == "security":
            self._report_security_data(result.data)
        elif result.validator_name == "container_ubi":
            self._report_container_ubi_data(result.data)
        elif result.validator_name == "container_version":
            self._report_container_version_data(result.data)

        # Report errors and warnings if verbose or validator failed
        if result.errors and (self.verbose or not result.passed):
            for error in result.errors:
                print(f"    ❌ {error}")

        # Show warnings for container validators always, others only in verbose mode
        show_warnings = (
            self.verbose or result.validator_name.startswith("container_") or not result.passed
        )

        if result.warnings and show_warnings:
            for warning in result.warnings:
                print(f"    ⚠️  {warning}")

    def _report_protocol_data(self, data: dict[str, Any]) -> None:
        """Report protocol validation specific data."""
        server_info = data.get("server_info", {})
        if server_info:
            name = server_info.get("name", "Unknown")
            version = server_info.get("version", "Unknown")
            print(f"    🖥 Server: {name} v{version}")

        capabilities = data.get("capabilities", {})
        if capabilities:
            caps = list(capabilities.keys())
            print(f"    🔧 Capabilities: {', '.join(caps)}")

    def _report_capabilities_data(self, data: dict[str, Any]) -> None:
        """Report capabilities validation specific data."""
        tools = data.get("tools", [])
        if tools:
            print(f"    🔨 Tools ({len(tools)}): {', '.join(tools[:5])}")
            if len(tools) > 5:
                print(f"        ... and {len(tools) - 5} more")

        prompts = data.get("prompts", [])
        if prompts:
            print(f"    💬 Prompts ({len(prompts)}): {', '.join(prompts[:5])}")
            if len(prompts) > 5:
                print(f"        ... and {len(prompts) - 5} more")

        resources = data.get("resources", [])
        if resources:
            print(f"    📁 Resources ({len(resources)}): {', '.join(resources[:5])}")
            if len(resources) > 5:
                print(f"        ... and {len(resources) - 5} more")

    def _report_ping_data(self, data: dict[str, Any]) -> None:
        """Report ping validation specific data."""
        if data.get("supported"):
            response_time = data.get("response_time_ms", 0)
            print(f"    🏓 Ping: Supported ({response_time:.2f}ms)")
        elif data.get("error"):
            error = data.get("error")
            if "not supported" in error.lower():
                print("    🏓 Ping: Not supported (optional)")
            else:
                print(f"    🏓 Ping: {error}")

    def _report_errors_data(self, data: dict[str, Any]) -> None:
        """Report error compliance validation specific data."""
        invalid_method = data.get("invalid_method_test", {})
        malformed_request = data.get("malformed_request_test", {})
        compliance_issues = data.get("compliance_issues", [])

        passed_tests = []
        if invalid_method.get("passed"):
            passed_tests.append("invalid method")
        if malformed_request.get("passed"):
            passed_tests.append("malformed request")

        if passed_tests:
            tests_str = " & ".join(passed_tests)
            print(f"    ⚠️  Error compliance: {tests_str} handling validated")

        if compliance_issues:
            issue_count = len(compliance_issues)
            print(f"    ⚠️  Error compliance: {issue_count} format issue(s) detected")

    def _report_security_data(self, data: dict[str, Any]) -> None:
        """Report security validation specific data."""
        tools_scanned = data.get("tools_scanned", 0)
        vulnerabilities_found = data.get("vulnerabilities_found", 0)
        issues_found = data.get("issues_found", 0)

        total_issues = vulnerabilities_found + issues_found

        if total_issues > 0:
            print(f"    🔍 Security: {total_issues} issues found in {tools_scanned} tools")

            # Show vulnerability types if available
            vuln_types = data.get("vulnerability_types", [])
            if vuln_types:
                types_str = ", ".join(vuln_types[:3])
                if len(vuln_types) > 3:
                    types_str += f" (and {len(vuln_types) - 3} more)"
                print(f"        🚨 Vulnerabilities: {types_str}")

            # Show issue codes if available
            issue_codes = data.get("issue_codes", [])
            if issue_codes:
                codes_str = ", ".join(issue_codes[:3])
                if len(issue_codes) > 3:
                    codes_str += f" (and {len(issue_codes) - 3} more)"
                print(f"        🚨 Issues: {codes_str}")
        else:
            print(f"    🔍 Security: No issues found in {tools_scanned} tools")

        scan_file = data.get("scan_file")
        if scan_file:
            print(f"        💾 Report saved: {scan_file}")

    def _report_container_ubi_data(self, data: dict[str, Any]) -> None:
        """Report container UBI validation specific data."""
        image_name = data.get("image_name", "Unknown")
        base_image = data.get("base_image", "Unknown")
        is_ubi_based = data.get("is_ubi_based", False)
        rhel_version = data.get("rhel_version")

        print(f"    🐳 Container Image: {image_name}")

        if is_ubi_based:
            if rhel_version:
                print(f"    ✅ UBI Base: {base_image} (RHEL {rhel_version})")
            else:
                print(f"    ✅ UBI Base: {base_image}")
        else:
            print(f"    📦 Base Image: {base_image} (Non-UBI)")

    def _report_container_version_data(self, data: dict[str, Any]) -> None:
        """Report container version validation specific data."""
        image_name = data.get("image_name", "Unknown")
        image_tag = data.get("image_tag", "Unknown")
        using_latest = data.get("using_latest", False)

        print(f"    🐳 Container Image: {image_name}")

        if using_latest:
            print(f"    ✅ Version: {image_tag} (Latest)")
        else:
            print(f"    📌 Version: {image_tag} (Specific tag)")


def print_profile_info(config_manager) -> None:
    """Print information about available profiles."""
    print("Available validation profiles:")
    for profile_name in config_manager.list_profiles():
        profile = config_manager.profiles[profile_name]
        active_marker = " (active)" if profile_name == config_manager.active_profile else ""
        print(f"  {profile_name}{active_marker}: {profile.description}")

        if profile_name == config_manager.active_profile:
            enabled_validators = [
                name for name, config in profile.validators.items() if config.enabled
            ]
            print(f"    Validators: {', '.join(enabled_validators)}")


def print_validator_info(orchestrator) -> None:
    """Print information about available validators."""
    print("Available validators:")
    for validator_name in orchestrator.registry.list_validators():
        validator = orchestrator.registry.create_validator(validator_name)
        if validator:
            print(f"  {validator_name}: {validator.description}")
            if validator.dependencies:
                print(f"    Dependencies: {', '.join(validator.dependencies)}")
