"""Transport layer for MCP communication."""

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class MCPTransport(ABC):
    """Abstract base class for MCP transport implementations."""

    @abstractmethod
    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC request."""
        pass

    @abstractmethod
    async def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification."""
        pass

    @abstractmethod
    async def send_and_receive(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0
    ) -> Dict[str, Any]:
        """Send request and wait for response."""
        pass

    @abstractmethod
    async def read_response(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Read and parse a response."""
        pass

    @abstractmethod
    def parse_response(self, response_line: str) -> Dict[str, Any]:
        """Parse a response line."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""
        pass


class StdioTransport(MCPTransport):
    """Handles JSON-RPC communication with MCP servers via stdio."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self.request_id = 0

    def _get_next_id(self) -> int:
        """Get next request ID for JSON-RPC."""
        self.request_id += 1
        return self.request_id

    def create_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Create a JSON-RPC 2.0 request."""
        request = {"jsonrpc": "2.0", "id": self._get_next_id(), "method": method}
        if params:
            request["params"] = params
        return json.dumps(request) + "\n"

    def create_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> str:
        """Create a JSON-RPC 2.0 notification (no response expected)."""
        notification = {"jsonrpc": "2.0", "method": method}
        if params:
            notification["params"] = params
        return json.dumps(notification) + "\n"

    def parse_response(self, response_line: str) -> Dict[str, Any]:
        """Parse a JSON-RPC response line."""
        try:
            return json.loads(response_line.strip())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}") from e

    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC request."""
        request = self.create_request(method, params)
        self.process.stdin.write(request.encode())
        await self.process.stdin.drain()

    async def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification."""
        notification = self.create_notification(method, params)
        self.process.stdin.write(notification.encode())
        await self.process.stdin.drain()

    async def read_response(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Read and parse a JSON-RPC response."""
        response_line = await asyncio.wait_for(self.process.stdout.readline(), timeout=timeout)
        return self.parse_response(response_line.decode())

    async def send_and_receive(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0
    ) -> Dict[str, Any]:
        """Send request and wait for response."""
        await self.send_request(method, params)
        return await self.read_response(timeout)

    async def close(self) -> None:
        """Close the transport connection."""
        if self.process and self.process.returncode is None:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()
            except Exception:
                pass

            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


# Legacy alias for backward compatibility
JSONRPCTransport = StdioTransport
