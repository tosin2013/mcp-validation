"""Debug utilities for MCP validation."""

import os
import shlex
import sys
from typing import Any, Dict, List, Optional

# Global debug state - set by CLI argument
_debug_enabled = False


def set_debug_enabled(enabled: bool) -> None:
    """Set the global debug state."""
    global _debug_enabled
    _debug_enabled = enabled


def debug_log(message: str, level: str = "INFO", category: str = "GENERAL") -> None:
    """Log debug messages if debug mode is enabled."""
    if is_debug_enabled():
        timestamp = get_timestamp()
        prefix = f"[{timestamp}] [{category}-{level}]"
        print(f"{prefix} {message}", file=sys.stderr)


def is_debug_enabled() -> bool:
    """Check if debug mode is enabled via CLI --debug flag."""
    global _debug_enabled
    return _debug_enabled


def get_timestamp() -> str:
    """Get current timestamp for debug messages."""
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def format_command_for_display(command_args: List[str]) -> str:
    """Format command arguments for safe display."""
    if not command_args:
        return "<empty command>"

    # Use shlex.quote to properly escape arguments for display
    quoted_args = [shlex.quote(arg) for arg in command_args]
    return " ".join(quoted_args)


def get_execution_context() -> Dict[str, Any]:
    """Get current execution context for debugging."""
    return {
        "current_directory": os.getcwd(),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "user": os.environ.get("USER", os.environ.get("USERNAME", "unknown")),
        "shell": os.environ.get("SHELL", "unknown"),
        "path_directories": len(os.environ.get("PATH", "").split(os.pathsep)),
    }


def log_execution_start(command_args: List[str], env_vars: Optional[Dict[str, str]] = None) -> None:
    """Log the start of process execution with full context."""
    if not is_debug_enabled():
        return

    context = get_execution_context()
    command_display = format_command_for_display(command_args)

    debug_log("=" * 80, "INFO", "EXEC")
    debug_log("🚀 Starting MCP Server Process", "INFO", "EXEC")
    debug_log("=" * 80, "INFO", "EXEC")

    # Execution context
    debug_log(f"📁 Working Directory: {context['current_directory']}", "INFO", "EXEC")
    debug_log(
        f"🐍 Python: {context['python_executable']} (v{context['python_version']})", "INFO", "EXEC"
    )
    debug_log(f"💻 Platform: {context['platform']}", "INFO", "EXEC")
    debug_log(f"👤 User: {context['user']}", "INFO", "EXEC")
    debug_log(f"🔧 Shell: {context['shell']}", "INFO", "EXEC")
    debug_log(f"📍 PATH entries: {context['path_directories']}", "INFO", "EXEC")

    # Command details
    debug_log("", "INFO", "EXEC")  # Empty line for readability
    debug_log("🔧 Command Execution Details:", "INFO", "EXEC")
    debug_log(f"   Command: {command_display}", "INFO", "EXEC")
    debug_log(f"   Arguments count: {len(command_args)}", "INFO", "EXEC")

    if len(command_args) > 1:
        debug_log(f"   Executable: {command_args[0]}", "INFO", "EXEC")
        debug_log(f"   Arguments: {format_command_for_display(command_args[1:])}", "INFO", "EXEC")

    # Environment variables
    if env_vars:
        debug_log("", "INFO", "EXEC")  # Empty line
        debug_log("🌍 Environment Variables:", "INFO", "EXEC")
        for key, value in sorted(env_vars.items()):
            # Mask sensitive values
            display_value = mask_sensitive_value(key, value)
            debug_log(f"   {key}={display_value}", "INFO", "EXEC")
    else:
        debug_log("   No custom environment variables", "INFO", "EXEC")

    debug_log("=" * 80, "INFO", "EXEC")


def log_execution_step(step: str, details: str = "") -> None:
    """Log a step in the execution process."""
    if not is_debug_enabled():
        return

    message = f"🔄 {step}"
    if details:
        message += f": {details}"
    debug_log(message, "INFO", "EXEC")


def log_execution_result(success: bool, details: str = "") -> None:
    """Log the result of process execution."""
    if not is_debug_enabled():
        return

    status_icon = "✅" if success else "❌"
    status_text = "SUCCESS" if success else "FAILED"
    level = "INFO" if success else "ERROR"

    message = f"{status_icon} Process execution {status_text}"
    if details:
        message += f": {details}"
    debug_log(message, level, "EXEC")


def mask_sensitive_value(key: str, value: str) -> str:
    """Mask sensitive environment variable values."""
    sensitive_patterns = [
        "password",
        "secret",
        "key",
        "token",
        "auth",
        "credential",
        "api_key",
        "client_secret",
        "private",
        "cert",
        "oauth",
    ]

    key_lower = key.lower()
    for pattern in sensitive_patterns:
        if pattern in key_lower:
            if len(value) <= 4:
                return "*" * len(value)
            else:
                return value[:2] + "*" * (len(value) - 4) + value[-2:]

    return value


def log_validator_progress(validator_name: str, step: str, details: str = "") -> None:
    """Log validator execution progress."""
    if not is_debug_enabled():
        return

    message = f"🔍 [{validator_name}] {step}"
    if details:
        message += f": {details}"
    debug_log(message, "INFO", "VALIDATOR")


def log_validation_summary(
    total_validators: int, passed: int, failed: int, execution_time: float
) -> None:
    """Log validation session summary."""
    if not is_debug_enabled():
        return

    debug_log("", "INFO", "SUMMARY")  # Empty line
    debug_log("📊 Validation Summary:", "INFO", "SUMMARY")
    debug_log(f"   Total validators: {total_validators}", "INFO", "SUMMARY")
    debug_log(f"   Passed: {passed}", "INFO", "SUMMARY")
    debug_log(f"   Failed: {failed}", "INFO", "SUMMARY")
    debug_log(
        f"   Success rate: {(passed/total_validators*100):.1f}%" if total_validators > 0 else "N/A",
        "INFO",
        "SUMMARY",
    )
    debug_log(f"   Execution time: {execution_time:.2f}s", "INFO", "SUMMARY")
