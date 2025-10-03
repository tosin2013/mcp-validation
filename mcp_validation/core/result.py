"""Result data structures for MCP validation."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPValidationResult:
    """Legacy result structure for backward compatibility."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    server_info: dict[str, Any]
    capabilities: dict[str, Any]
    execution_time: float
    tools: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    mcp_scan_results: dict[str, Any] | None = None
    checklist: dict[str, Any] | None = None
    mcp_scan_file: str | None = None
    ping_result: dict[str, Any] | None = None
    error_compliance: dict[str, Any] | None = None


@dataclass
class ValidatorResult:
    """Result from a single validator execution."""

    validator_name: str
    passed: bool
    errors: list[str]
    warnings: list[str]
    data: dict[str, Any]
    execution_time: float


@dataclass
class ValidationSession:
    """Complete validation session result."""

    profile_name: str
    overall_success: bool
    execution_time: float
    validator_results: list[ValidatorResult]
    errors: list[str]
    warnings: list[str]
    command_args: list[str] = None

    def to_legacy_result(self) -> MCPValidationResult:
        """Convert to legacy MCPValidationResult for backward compatibility."""
        # Aggregate data from validator results
        server_info = {}
        capabilities = {}
        tools = []
        prompts = []
        resources = []
        ping_result = None
        error_compliance = None
        mcp_scan_results = None
        mcp_scan_file = None
        checklist = {}

        for result in self.validator_results:
            # Extract specific data based on validator type
            if result.validator_name == "protocol":
                server_info.update(result.data.get("server_info", {}))
                capabilities.update(result.data.get("capabilities", {}))

            elif result.validator_name == "capabilities":
                tools.extend(result.data.get("tools", []))
                prompts.extend(result.data.get("prompts", []))
                resources.extend(result.data.get("resources", []))

            elif result.validator_name == "ping":
                ping_result = result.data

            elif result.validator_name == "errors":
                error_compliance = result.data

            elif result.validator_name == "security":
                mcp_scan_results = result.data.get("scan_results")
                mcp_scan_file = result.data.get("scan_file")

            # Build checklist
            checklist[result.validator_name] = {
                "status": "passed" if result.passed else "failed",
                "details": f"{result.validator_name.title()} validation",
                "execution_time": result.execution_time,
            }

        return MCPValidationResult(
            is_valid=self.overall_success,
            errors=self.errors,
            warnings=self.warnings,
            server_info=server_info,
            capabilities=capabilities,
            execution_time=self.execution_time,
            tools=tools,
            prompts=prompts,
            resources=resources,
            mcp_scan_results=mcp_scan_results,
            checklist=checklist,
            mcp_scan_file=mcp_scan_file,
            ping_result=ping_result,
            error_compliance=error_compliance,
        )
