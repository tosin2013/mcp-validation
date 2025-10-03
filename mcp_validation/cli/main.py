"""Enhanced CLI interface for MCP validation."""

import argparse
import asyncio
import sys
from typing import Dict, List, Optional

from ..config.settings import ConfigurationManager, load_config_from_env
from ..core.validator import MCPValidationOrchestrator
from ..reporting.console import ConsoleReporter, print_profile_info, print_validator_info
from ..reporting.json_report import JSONReporter


def parse_env_args(env_args: List[str]) -> Dict[str, str]:
    """Parse environment variable arguments in KEY=VALUE format."""
    env_vars = {}
    for env_arg in env_args:
        if "=" not in env_arg:
            raise ValueError(f"Environment variable must be in KEY=VALUE format: {env_arg}")
        key, value = env_arg.split("=", 1)
        env_vars[key] = value
    return env_vars


def detect_runtime_command(command_args: List[str]) -> Optional[str]:
    """Auto-detect runtime command from MCP server command arguments."""
    if not command_args:
        return None

    first_command = command_args[0]

    # Direct runtime commands
    runtime_commands = {
        "uv",
        "docker",
        "podman",
        "npx",
        "node",
        "python",
        "python3",
        "pip",
        "java",
        "mvn",
        "gradle",
        "go",
        "cargo",
        "rust",
    }

    if first_command in runtime_commands:
        return first_command

    # Check for common patterns
    if first_command.endswith("python") or first_command.endswith("python3"):
        return "python3" if "python3" in first_command else "python"

    if first_command.endswith("node"):
        return "node"

    # Check for shebang or script patterns
    if first_command.startswith("./") or first_command.endswith(".py"):
        return "python3"  # Assume Python for .py files

    if first_command.endswith(".js") or first_command.endswith(".mjs"):
        return "node"

    # Check for container run patterns
    if (
        len(command_args) >= 2
        and first_command in ["docker", "podman"]
        and command_args[1] == "run"
    ):
        return first_command

    return None


def is_container_runtime_command(command_args: List[str]) -> bool:
    """Check if command is a container runtime command (docker/podman run)."""
    if not command_args or len(command_args) < 3:
        return False

    return command_args[0] in ["docker", "podman"] and command_args[1] == "run"


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the enhanced argument parser."""
    parser = argparse.ArgumentParser(
        description="Validate MCP server protocol compliance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration Examples:
  # Use default comprehensive profile (stdio transport)
  mcp-validate -- npx @dynatrace-oss/dynatrace-mcp-server

  # Use HTTP transport
  mcp-validate --transport http --endpoint http://localhost:3000/mcp

  # Use specific profile with HTTP transport
  mcp-validate --profile security_focused --transport http --endpoint http://localhost:3000/mcp

  # Use custom config file
  mcp-validate --config ./my-config.json -- node server.js

  # Override specific validators
  mcp-validate --enable ping --disable security -- ./server

  # List available profiles and validators
  mcp-validate --list-profiles
  mcp-validate --list-validators

Environment Variables:
  MCP_VALIDATION_CONFIG    - Path to configuration file
  MCP_VALIDATION_PROFILE   - Active profile name
        """,
    )

    # Command arguments
    parser.add_argument("command", nargs="*", help="Command and arguments to run the MCP server")

    # Configuration options
    parser.add_argument("--config", metavar="FILE", help="Configuration file path")

    parser.add_argument("--profile", metavar="NAME", help="Validation profile to use")

    # Environment variables
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set environment variable (can be used multiple times)",
    )

    # Validator control
    parser.add_argument(
        "--enable",
        action="append",
        default=[],
        metavar="VALIDATOR",
        help="Enable specific validator",
    )

    parser.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="VALIDATOR",
        help="Disable specific validator",
    )

    # Information commands
    parser.add_argument(
        "--list-profiles", action="store_true", help="List available validation profiles"
    )

    parser.add_argument("--list-validators", action="store_true", help="List available validators")

    # Output options
    parser.add_argument(
        "--json-report", metavar="FILENAME", help="Export detailed JSON report to specified file"
    )

    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed output including warnings"
    )

    parser.add_argument(
        "--debug", action="store_true", help="Enable detailed debug output with execution tracking"
    )

    parser.add_argument("--timeout", type=float, metavar="SECONDS", help="Global timeout override")

    # Security options
    parser.add_argument(
        "--skip-mcp-scan", action="store_true", help="Skip mcp-scan security analysis"
    )

    # Repository validation options
    parser.add_argument(
        "--repo-url", metavar="URL", help="Repository URL to validate for OSS compliance"
    )

    # Runtime validation options
    parser.add_argument(
        "--runtime-command",
        metavar="COMMAND",
        help="Runtime command to validate (e.g., uv, docker, npx). If not specified, will be auto-detected from the MCP server command.",
    )

    # Transport options
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport type to use for MCP communication (default: stdio)",
    )

    parser.add_argument(
        "--endpoint",
        metavar="URL",
        help="HTTP endpoint URL for http transport (e.g., http://localhost:3000/mcp)",
    )

    return parser


async def main():
    """Enhanced CLI entry point."""
    parser = create_argument_parser()
    args = parser.parse_args()

    try:
        # Load configuration
        if args.config:
            config_manager = ConfigurationManager(args.config)
        else:
            config_manager = load_config_from_env()

        # Handle information commands
        if args.list_profiles:
            print_profile_info(config_manager)
            return 0

        orchestrator = MCPValidationOrchestrator(config_manager)

        if args.list_validators:
            print_validator_info(orchestrator)
            return 0

        # Validate transport-specific arguments
        if args.transport == "stdio":
            if not args.command:
                parser.error("Command arguments required for stdio transport")
            if args.endpoint:
                parser.error("--endpoint is not valid for stdio transport")
        elif args.transport == "http":
            if not args.endpoint:
                parser.error("--endpoint is required for http transport")
            if not args.endpoint.startswith(("http://", "https://")):
                parser.error("--endpoint must be a valid HTTP URL (http:// or https://)")
        else:
            parser.error(f"Unsupported transport: {args.transport}")

        # Apply command-line overrides
        if args.profile:
            config_manager.set_active_profile(args.profile)

        # Override validator enables/disables
        active_profile = config_manager.get_active_profile()
        for validator_name in args.enable:
            if validator_name in active_profile.validators:
                active_profile.validators[validator_name].enabled = True
            else:
                print(f"Warning: Unknown validator '{validator_name}' - ignoring")

        for validator_name in args.disable:
            if validator_name in active_profile.validators:
                active_profile.validators[validator_name].enabled = False
            else:
                print(f"Warning: Unknown validator '{validator_name}' - ignoring")

        # Disable security if --skip-mcp-scan is used
        if args.skip_mcp_scan and "security" in active_profile.validators:
            active_profile.validators["security"].enabled = False

        # Enable repository validators if --repo-url is provided
        if args.repo_url:
            # Add repo validators to profile if not already present
            if "repo_availability" not in active_profile.validators:
                from ..config.settings import ValidatorConfig

                active_profile.validators["repo_availability"] = ValidatorConfig(
                    enabled=True,
                    required=True,
                    timeout=30.0,
                    parameters={"repo_url": args.repo_url, "clone_timeout": 30.0},
                )
            else:
                active_profile.validators["repo_availability"].enabled = True
                active_profile.validators["repo_availability"].parameters[
                    "repo_url"
                ] = args.repo_url

            if "license" not in active_profile.validators:
                from ..config.settings import ValidatorConfig

                active_profile.validators["license"] = ValidatorConfig(
                    enabled=True,
                    required=True,
                    timeout=30.0,
                    parameters={"repo_url": args.repo_url, "clone_timeout": 30.0},
                )
            else:
                active_profile.validators["license"].enabled = True
                active_profile.validators["license"].parameters["repo_url"] = args.repo_url

        # Enable runtime validators based on command or --runtime-command
        runtime_command = args.runtime_command or detect_runtime_command(args.command)
        if runtime_command:
            # Add runtime validators to profile if not already present
            if "runtime_exists" not in active_profile.validators:
                from ..config.settings import ValidatorConfig

                active_profile.validators["runtime_exists"] = ValidatorConfig(
                    enabled=True,
                    required=True,
                    timeout=10.0,
                    parameters={"runtime_command": runtime_command},
                )
            else:
                active_profile.validators["runtime_exists"].enabled = True
                active_profile.validators["runtime_exists"].parameters[
                    "runtime_command"
                ] = runtime_command

            if "runtime_executable" not in active_profile.validators:
                from ..config.settings import ValidatorConfig

                active_profile.validators["runtime_executable"] = ValidatorConfig(
                    enabled=True,
                    required=True,
                    timeout=10.0,
                    parameters={"runtime_command": runtime_command, "execution_timeout": 10.0},
                )
            else:
                active_profile.validators["runtime_executable"].enabled = True
                active_profile.validators["runtime_executable"].parameters[
                    "runtime_command"
                ] = runtime_command

        # Enable container validators for container runtime commands
        if is_container_runtime_command(args.command):
            # Add container UBI validator
            if "container_ubi" not in active_profile.validators:
                from ..config.settings import ValidatorConfig

                active_profile.validators["container_ubi"] = ValidatorConfig(
                    enabled=True,
                    required=False,
                    timeout=60.0,
                    parameters={"warn_only_for_non_ubi": True},
                )
            else:
                active_profile.validators["container_ubi"].enabled = True
                # Ensure warn_only is set to True by default
                if (
                    "warn_only_for_non_ubi"
                    not in active_profile.validators["container_ubi"].parameters
                ):
                    active_profile.validators["container_ubi"].parameters[
                        "warn_only_for_non_ubi"
                    ] = True

            # Add container version validator
            if "container_version" not in active_profile.validators:
                from ..config.settings import ValidatorConfig

                active_profile.validators["container_version"] = ValidatorConfig(
                    enabled=True, required=False, timeout=30.0, parameters={}
                )
            else:
                active_profile.validators["container_version"].enabled = True

        # Override timeout
        if args.timeout:
            active_profile.global_timeout = args.timeout

        # Parse environment variables
        env_vars = parse_env_args(args.env) if args.env else None

        # Display what we're testing
        print(f"Transport: {args.transport}")
        if args.transport == "stdio" and args.command:
            print(f"Testing MCP server: {' '.join(args.command)}")
        elif args.transport == "http":
            print(f"Testing MCP endpoint: {args.endpoint}")
        print(f"Using profile: {active_profile.name}")
        if args.repo_url:
            print(f"Repository URL: {args.repo_url}")
        if runtime_command:
            print(f"Runtime command: {runtime_command}")
        if args.command and is_container_runtime_command(args.command):
            print("Container runtime detected: Container image validation enabled")
        if env_vars:
            print("Environment variables:")
            for key, value in env_vars.items():
                # Mask potential secrets in output
                display_value = value if len(value) < 20 else f"{value[:10]}..."
                print(f"  {key}={display_value}")
        print()

        # Run validation
        session = await orchestrator.validate_server(
            command_args=args.command,
            env_vars=env_vars,
            profile_name=args.profile,
            debug=args.debug,
            transport_type=args.transport,
            endpoint=args.endpoint,
        )

        # Display results
        console_reporter = ConsoleReporter(verbose=args.verbose)
        console_reporter.report_session(session)

        # Generate JSON report if requested
        if args.json_report:
            json_reporter = JSONReporter()
            # Use the final command args from the session (includes injected -e options for containers)
            final_command_args = session.command_args if session.command_args else args.command
            json_reporter.save_report(session, args.json_report, final_command_args, env_vars)

        # Exit with appropriate code
        return 0 if session.overall_success else 1

    except ValueError as e:
        print(f"❌ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n🛑 Validation interrupted")
        return 1
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        if args.debug:
            import traceback

            traceback.print_exc()
        return 1


def cli_main():
    """Synchronous entry point for CLI script."""
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n🛑 Validation interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
