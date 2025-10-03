"""HTTP transport implementation for MCP communication."""

import asyncio
import json
from typing import Any, Dict, Optional
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:
    aiohttp = None

from .transport import MCPTransport


class HTTPTransport(MCPTransport):
    """HTTP-based MCP transport with SSE support."""

    def __init__(self, endpoint: str):
        if aiohttp is None:
            raise ImportError(
                "aiohttp is required for HTTP transport. Install with: pip install aiohttp"
            )

        self.endpoint = endpoint
        self.session_id: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.request_id = 0
        self._initialized = False

        # Validate endpoint URL
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid endpoint URL: {endpoint}")

    def _get_next_id(self) -> int:
        """Get next request ID for JSON-RPC."""
        self.request_id += 1
        return self.request_id

    async def initialize(self) -> None:
        """Initialize HTTP session and MCP protocol."""
        if self._initialized:
            return

        # Create HTTP session
        self.session = aiohttp.ClientSession()

        # Establish MCP session via initialize sequence
        await self._establish_session()
        self._initialized = True

    async def _establish_session(self) -> None:
        """Establish MCP session via initialize sequence."""
        # Send InitializeRequest
        init_params = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"roots": {"listChanged": True}},
            "clientInfo": {"name": "mcp-validate", "version": "2.0.0"}
        }

        response = await self._post_request("initialize", init_params)

        # Check response status
        if response.status != 200:
            error_text = await response.text()
            raise ValueError(f"HTTP {response.status}: {error_text}")

        # Extract session ID from headers if provided
        if "Mcp-Session-Id" in response.headers:
            self.session_id = response.headers["Mcp-Session-Id"]

        # Parse the response to get the initialization result
        try:
            result = await response.json()
        except Exception as e:
            content_type = response.headers.get('Content-Type', 'unknown')
            raise ValueError(f"Failed to parse JSON response (Content-Type: {content_type}): {e}")

        # Send InitializedNotification
        await self._post_notification("notifications/initialized")

    async def _post_request(self, method: str, params: Dict[str, Any]) -> aiohttp.ClientResponse:
        """Send POST request with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"
        }

        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request_data = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": method,
            "params": params
        }

        try:
            response = await self.session.post(
                self.endpoint,
                json=request_data,
                headers=headers
            )
        except Exception as e:
            raise ValueError(f"Failed to connect to {self.endpoint}: {e}")

        # Handle session ID from response headers
        if "Mcp-Session-Id" in response.headers and not self.session_id:
            self.session_id = response.headers["Mcp-Session-Id"]

        return response

    async def _post_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send POST notification with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18"
        }

        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request_data = {
            "jsonrpc": "2.0",
            "method": method
        }

        if params:
            request_data["params"] = params

        try:
            response = await self.session.post(
                self.endpoint,
                json=request_data,
                headers=headers
            )
        except Exception as e:
            raise ValueError(f"Failed to send notification to {self.endpoint}: {e}")

        # Expect 202 Accepted for notifications
        if response.status != 202:
            error_text = await response.text()
            raise ValueError(f"Unexpected response status for notification: {response.status} - {error_text}")

    async def _handle_response(self, response: aiohttp.ClientResponse) -> Dict[str, Any]:
        """Handle HTTP response, supporting both JSON and SSE."""
        if response.content_type == "text/event-stream":
            # Handle SSE stream
            return await self._handle_sse_response(response)
        else:
            # Handle regular JSON response
            return await response.json()

    async def _handle_sse_response(self, response: aiohttp.ClientResponse) -> Dict[str, Any]:
        """Handle SSE stream response."""
        async for line in response.content:
            line_str = line.decode().strip()
            if line_str.startswith("data: "):
                data = line_str[6:].strip()
                if data:
                    return json.loads(data)

        raise ValueError("No data received from SSE stream")

    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC request."""
        if not self._initialized:
            await self.initialize()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"
        }

        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        request_data = {
            "jsonrpc": "2.0",
            "id": self._get_next_id(),
            "method": method
        }

        if params:
            request_data["params"] = params

        await self.session.post(
            self.endpoint,
            json=request_data,
            headers=headers
        )

    async def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification."""
        if not self._initialized:
            await self.initialize()

        await self._post_notification(method, params)

    async def send_and_receive(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0
    ) -> Dict[str, Any]:
        """Send request and wait for response."""
        if not self._initialized:
            await self.initialize()

        response = await self._post_request(method, params or {})

        # Handle the response based on content type
        result = await self._handle_response(response)

        # Return the result part if it's a JSON-RPC response
        if isinstance(result, dict) and "result" in result:
            return result["result"]

        return result

    async def read_response(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Read and parse a response."""
        # For HTTP transport, this is typically called after send_request
        # In HTTP, we don't have a separate read step like stdio
        raise NotImplementedError("HTTP transport uses send_and_receive instead of separate read_response")

    def parse_response(self, response_line: str) -> Dict[str, Any]:
        """Parse a response line."""
        try:
            return json.loads(response_line.strip())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}") from e

    async def close(self) -> None:
        """Close the transport connection."""
        if self.session and not self.session.closed:
            # Optionally send session termination
            if self.session_id:
                try:
                    headers = {"Mcp-Session-Id": self.session_id}
                    await self.session.delete(self.endpoint, headers=headers)
                except Exception:
                    # Ignore errors during cleanup
                    pass

            try:
                await self.session.close()
            except Exception:
                # Ignore errors during session close
                pass

        self.session = None
        self.session_id = None
        self._initialized = False