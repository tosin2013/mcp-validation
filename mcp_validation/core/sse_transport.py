"""SSE transport implementation for MCP communication."""

import asyncio
import json
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

from ..utils.debug import verbose_log
from .transport import MCPTransport


class SSETransport(MCPTransport):
    """SSE-based MCP transport using MCP SDK's sse_client."""

    def __init__(self, endpoint: str, auth_token: str | None = None):
        self.endpoint = endpoint
        self.auth_token = auth_token

        # MCP SDK transport streams and session
        self.read_stream = None
        self.write_stream = None
        self._connection_context = None
        self._client_session: ClientSession | None = None
        self._session_context = None
        self._initialized = False

    def _extract_error_details(self, error: Exception) -> str:
        """Extract detailed error information from complex exceptions like TaskGroup and ExceptionGroup."""
        # Handle ExceptionGroup (Python 3.11+)
        if hasattr(error, "exceptions") and error.exceptions:
            # Extract all meaningful exceptions
            error_messages = []
            for sub_error in error.exceptions:
                # Recursively extract error details from sub-exceptions
                sub_details = self._extract_error_details(sub_error)
                error_messages.append(sub_details)

            if error_messages:
                return "; ".join(error_messages)

        # Check for __cause__ chain
        if hasattr(error, "__cause__") and error.__cause__:
            return f"{type(error.__cause__).__name__}: {error.__cause__}"

        # Check for __context__ chain
        if hasattr(error, "__context__") and error.__context__:
            return f"{type(error.__context__).__name__}: {error.__context__}"

        # Fallback to the original error
        return f"{type(error).__name__}: {error}"

    async def initialize(self) -> None:
        """Initialize SSE connection using MCP SDK's sse_client."""
        if self._initialized:
            return

        verbose_log(f"🔗 Initializing SSE transport to {self.endpoint}")

        try:
            # Prepare headers for SSE connection
            headers = {
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
            }

            # Add authorization header if token provided
            auth = None
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"
                verbose_log(f"🔑 Using auth token for SSE: {self.auth_token[:10]}...")

            # Use MCP SDK's sse_client
            verbose_log("📡 Opening SSE connection...")
            self._connection_context = sse_client(
                url=self.endpoint,
                headers=headers,
                auth=auth,
                timeout=10.0,  # HTTP timeout
                sse_read_timeout=300.0,  # 5 minutes for SSE reads
            )

            # Enter the context and get streams
            try:
                streams = await self._connection_context.__aenter__()
                self.read_stream, self.write_stream = streams
                verbose_log("✅ SSE connection established")
            except Exception as connection_error:
                # Clean up connection context if it was created
                if self._connection_context:
                    try:
                        await self._connection_context.__aexit__(
                            type(connection_error), connection_error, connection_error.__traceback__
                        )
                    except Exception:
                        # Ignore cleanup errors to avoid masking the original error
                        pass
                    self._connection_context = None

                # Extract detailed error information first
                error_details = self._extract_error_details(connection_error)

                # Handle specific SSE connection errors
                if "401" in error_details or "Unauthorized" in error_details:
                    verbose_log(
                        f"❌ SSE 401 Unauthorized - authentication required: {error_details}"
                    )
                    raise ValueError(
                        f"Authentication required for {self.endpoint}. "
                        f"Token may be invalid or expired. Details: {error_details}"
                    ) from connection_error
                elif "403" in error_details or "Forbidden" in error_details:
                    verbose_log(f"❌ SSE 403 Forbidden - insufficient permissions: {error_details}")
                    raise ValueError(
                        f"Access forbidden for {self.endpoint}. "
                        f"Please check your token permissions. Details: {error_details}"
                    ) from connection_error
                else:
                    verbose_log(f"❌ SSE connection failed: {error_details}")
                    raise ValueError(
                        f"Failed to connect to {self.endpoint}: {error_details}"
                    ) from connection_error

            # Create and initialize ClientSession for protocol communication
            verbose_log("🤝 Creating MCP client session...")
            self._session_context = ClientSession(self.read_stream, self.write_stream)
            self._client_session = await self._session_context.__aenter__()

            verbose_log("⚡ Initializing MCP session...")
            try:
                await self._client_session.initialize()
            except Exception as session_error:
                # Handle MCP session initialization errors
                if "401" in str(session_error) or "Unauthorized" in str(session_error):
                    verbose_log("❌ MCP session initialization failed: 401 Unauthorized")
                    raise ValueError(
                        f"Authentication required for MCP endpoint {self.endpoint}. "
                        f"Please provide a valid auth token using --auth-token."
                    ) from session_error
                elif "403" in str(session_error) or "Forbidden" in str(session_error):
                    verbose_log("❌ MCP session initialization failed: 403 Forbidden")
                    raise ValueError(
                        f"Access forbidden for MCP endpoint {self.endpoint}. "
                        f"Please check your token permissions."
                    ) from session_error
                else:
                    verbose_log(f"❌ MCP session initialization failed: {session_error}")
                    raise ValueError(
                        f"Failed to initialize MCP session: {session_error}"
                    ) from session_error

            verbose_log("✅ SSE transport and MCP session initialized successfully")
            self._initialized = True

        except Exception as e:
            # Extract detailed error information
            error_details = self._extract_error_details(e)
            verbose_log(f"❌ Failed to initialize SSE transport: {error_details}")

            # Clean up any partially initialized resources
            if self._session_context:
                try:
                    await self._session_context.__aexit__(type(e), e, e.__traceback__)
                except Exception:
                    pass
                self._session_context = None
                self._client_session = None

            if self._connection_context:
                try:
                    await self._connection_context.__aexit__(type(e), e, e.__traceback__)
                except Exception:
                    pass
                self._connection_context = None

            raise ValueError(f"Failed to initialize SSE transport: {error_details}") from e

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC request."""
        if not self._initialized:
            await self.initialize()

        if not self._client_session:
            raise ValueError("Transport not properly initialized - no client session available")

        verbose_log(f"📤 Sending request: {method}")
        # Note: This method is typically used for fire-and-forget requests
        # For most MCP operations, use send_and_receive instead

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification."""
        if not self._initialized:
            await self.initialize()

        if not self._client_session:
            raise ValueError("Transport not properly initialized - no client session available")

        verbose_log(f"📤 Sending notification: {method}")
        # Note: ClientSession doesn't have a generic notification method
        # For MCP-specific notifications, we'd need to use the appropriate methods

    async def send_and_receive(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0
    ) -> dict[str, Any]:
        """Send request and wait for response using MCP ClientSession."""
        if not self._initialized:
            await self.initialize()

        if not self._client_session:
            raise ValueError("Transport not properly initialized - no client session available")

        verbose_log(f"📤 Sending MCP request: {method}")

        # Use ClientSession methods for specific MCP operations
        try:
            if method == "initialize":
                # ClientSession was already initialized in transport.initialize()
                # Return successful initialization response in JSON-RPC format
                verbose_log("✅ Initialize request - session already initialized")
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {
                            "tools": {},
                            "resources": {},
                            "prompts": {},
                            "logging": {},
                        },
                        "serverInfo": {"name": "SSE MCP Server", "version": "unknown"},
                    },
                }
            elif method == "tools/list":
                result = await self._client_session.list_tools()
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"tools": [tool.model_dump() for tool in result.tools]},
                }
            elif method == "tools/call":
                tool_name = params.get("name") if params else None
                arguments = params.get("arguments", {}) if params else {}
                if not tool_name:
                    raise ValueError("Tool name is required for tools/call")
                result = await self._client_session.call_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [content.model_dump() for content in result.content]},
                }
            elif method == "resources/list":
                result = await self._client_session.list_resources()
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "resources": [resource.model_dump() for resource in result.resources]
                    },
                }
            elif method == "prompts/list":
                result = await self._client_session.list_prompts()
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"prompts": [prompt.model_dump() for prompt in result.prompts]},
                }
            elif method == "ping":
                # Simple ping test - just return success if session is working
                return {"jsonrpc": "2.0", "id": 1, "result": {"ping": "pong"}}
            else:
                # For other methods, we'll need to handle them as they come up
                verbose_log(f"⚠️ Unsupported method for ClientSession: {method}")
                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {
                        "code": -32601,
                        "message": f"Method {method} not supported in SSE transport",
                    },
                }

        except Exception as e:
            verbose_log(f"❌ MCP request failed: {e}")
            raise ValueError(f"MCP request failed: {e}") from e

    async def read_response(self, timeout: float = 5.0) -> dict[str, Any]:
        """Read and parse a response."""
        if not self.read_stream:
            raise ValueError("Transport not properly initialized - no read stream available")

        verbose_log("📥 Reading response...")
        response_message = await asyncio.wait_for(self.read_stream.receive(), timeout=timeout)
        return {"jsonrpc": "2.0", "result": response_message}

    def parse_response(self, response_line: str) -> dict[str, Any]:
        """Parse a response line."""
        try:
            return json.loads(response_line.strip())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}") from e

    async def close(self) -> None:
        """Close the transport connection."""
        verbose_log("🔄 Closing SSE transport...")

        # Close MCP client session first
        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
                verbose_log("✅ MCP client session closed")
            except Exception as e:
                verbose_log(f"⚠️ Error during MCP session cleanup: {e}")
            finally:
                self._session_context = None
                self._client_session = None

        # Close SSE connection context
        if self._connection_context:
            try:
                await self._connection_context.__aexit__(None, None, None)
                verbose_log("✅ SSE transport closed successfully")
            except Exception as e:
                verbose_log(f"⚠️ Error during SSE transport cleanup: {e}")
            finally:
                self._connection_context = None

        self.read_stream = None
        self.write_stream = None
        self._initialized = False
