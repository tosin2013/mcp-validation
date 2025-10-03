"""Transport factory for creating MCP transport instances."""

import asyncio
import os
from typing import List, Optional, Dict, Any

from .transport import MCPTransport
from .transport import StdioTransport
from .http_transport import HTTPTransport


class TransportFactory:
    """Factory for creating transport instances."""

    @staticmethod
    async def create_transport(
        transport_type: str,
        command_args: Optional[List[str]] = None,
        endpoint: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None
    ) -> MCPTransport:
        """Create and initialize transport based on type."""

        if transport_type == "stdio":
            if not command_args:
                raise ValueError("Command arguments required for stdio transport")
            return await TransportFactory._create_stdio_transport(command_args, env_vars)

        elif transport_type == "http":
            if not endpoint:
                raise ValueError("Endpoint URL required for http transport")
            return await TransportFactory._create_http_transport(endpoint)

        else:
            raise ValueError(f"Unsupported transport type: {transport_type}")

    @staticmethod
    async def _create_stdio_transport(
        command_args: List[str],
        env_vars: Optional[Dict[str, str]] = None
    ) -> StdioTransport:
        """Create stdio transport by launching subprocess."""
        from ..core.validator import _inject_container_env_vars

        # Set up environment
        env = os.environ.copy()
        final_command_args = command_args

        # Handle container environment variables
        if (
            env_vars
            and len(command_args) >= 2
            and command_args[0] in ["docker", "podman"]
            and command_args[1] == "run"
        ):
            final_command_args = _inject_container_env_vars(command_args, env_vars)
        elif env_vars:
            # For non-container commands, use environment variables in subprocess environment
            env.update(env_vars)

        # Create subprocess
        process = await asyncio.create_subprocess_exec(
            *final_command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        return StdioTransport(process)

    @staticmethod
    async def _create_http_transport(endpoint: str) -> HTTPTransport:
        """Create HTTP transport and initialize connection."""
        transport = HTTPTransport(endpoint)
        await transport.initialize()
        return transport

    @staticmethod
    def get_supported_transports() -> List[str]:
        """Get list of supported transport types."""
        return ["stdio", "http"]

    @staticmethod
    def validate_transport_args(
        transport_type: str,
        command_args: Optional[List[str]] = None,
        endpoint: Optional[str] = None
    ) -> None:
        """Validate transport arguments without creating transport."""
        if transport_type not in TransportFactory.get_supported_transports():
            raise ValueError(f"Unsupported transport type: {transport_type}")

        if transport_type == "stdio":
            if not command_args:
                raise ValueError("Command arguments required for stdio transport")
        elif transport_type == "http":
            if not endpoint:
                raise ValueError("Endpoint URL required for http transport")
            if not endpoint.startswith(("http://", "https://")):
                raise ValueError("Endpoint must be a valid HTTP URL (http:// or https://)")