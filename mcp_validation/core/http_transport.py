"""HTTP transport implementation for MCP communication."""

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

try:
    import aiohttp
except ImportError:
    aiohttp = None

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientMetadata

from .transport import MCPTransport


class SimpleTokenStorage(TokenStorage):
    """Simple in-memory token storage for OAuth tokens."""

    def __init__(self):
        self._tokens: dict[str, str] = {}

    async def get_token(self, token_type: str) -> str | None:
        """Get stored token by type."""
        return self._tokens.get(token_type)

    async def store_token(self, token_type: str, token: str) -> None:
        """Store token by type."""
        self._tokens[token_type] = token

    async def clear_tokens(self) -> None:
        """Clear all stored tokens."""
        self._tokens.clear()


class HTTPTransport(MCPTransport):
    """HTTP-based MCP transport with SSE and OAuth 2.0 support."""

    def __init__(self, endpoint: str, auth_token: str | None = None,
                 client_id: str | None = None, client_secret: str | None = None):
        if aiohttp is None:
            raise ImportError(
                "aiohttp is required for HTTP transport. Install with: pip install aiohttp"
            )

        self.endpoint = endpoint
        self.session_id: str | None = None
        self.session: aiohttp.ClientSession | None = None
        self.request_id = 0
        self._initialized = False

        # OAuth 2.0 configuration
        self.auth_token = auth_token
        self.client_id = client_id
        self.client_secret = client_secret

        # MCP OAuth provider
        self.mcp_oauth_provider: OAuthClientProvider | None = None
        self.token_storage: SimpleTokenStorage | None = None

        # Legacy OAuth discovery (fallback)
        self.authorization_server_metadata: dict[str, Any] | None = None
        self.protected_resource_metadata: dict[str, Any] | None = None

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

        # Setup OAuth provider if credentials provided
        if self.client_id and self.client_secret and not self.auth_token:
            await self._setup_mcp_oauth()
        elif not self.auth_token:
            # Fallback to legacy OAuth discovery
            await self._discover_oauth_configuration()

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

        # Check response status and handle OAuth errors
        if response.status == 401:
            # OAuth 2.0 unauthorized - check for WWW-Authenticate header
            www_auth = response.headers.get("WWW-Authenticate", "")
            error_text = await response.text()
            if "Bearer" in www_auth:
                raise ValueError(f"OAuth 2.0 authentication required. Server response: {www_auth}")
            else:
                raise ValueError(f"HTTP 401 Unauthorized: {error_text}")
        elif response.status == 403:
            error_text = await response.text()
            raise ValueError(f"HTTP 403 Forbidden - insufficient OAuth scopes or permissions: {error_text}")
        elif response.status != 200:
            error_text = await response.text()
            raise ValueError(f"HTTP {response.status}: {error_text}")

        # Extract session ID from headers if provided
        if "Mcp-Session-Id" in response.headers:
            self.session_id = response.headers["Mcp-Session-Id"]

        # Parse the response to get the initialization result
        try:
            await response.json()  # Validate JSON response
        except Exception as e:
            content_type = response.headers.get('Content-Type', 'unknown')
            raise ValueError(f"Failed to parse JSON response (Content-Type: {content_type}): {e}") from e

        # Send InitializedNotification
        await self._post_notification("notifications/initialized")

    async def _setup_mcp_oauth(self) -> None:
        """Setup MCP OAuth provider for authentication."""
        try:
            # Create token storage
            self.token_storage = SimpleTokenStorage()

            # Create client metadata for OAuth provider
            client_metadata = OAuthClientMetadata(
                client_id=self.client_id,
                client_secret=self.client_secret,
                grant_types=["client_credentials"],
                token_endpoint_auth_method="client_secret_basic"
            )

            # Simple callback handlers (not needed for client credentials flow)
            async def redirect_handler(url: str) -> None:
                del url  # Not needed for client credentials flow

            async def callback_handler() -> tuple[str, str | None]:
                return "", None  # No callback needed

            # Create OAuth provider
            self.mcp_oauth_provider = OAuthClientProvider(
                server_url=self.endpoint,
                client_metadata=client_metadata,
                storage=self.token_storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler
            )

            # Initialize the OAuth provider
            await self.mcp_oauth_provider.initialize()

            # Get access token
            await self.mcp_oauth_provider.ensure_token()

            # Store the token for use in requests
            access_token = await self.token_storage.get_token("access_token")
            if access_token:
                self.auth_token = access_token

        except Exception:
            # If MCP OAuth setup fails, continue without OAuth
            pass

    async def _discover_oauth_configuration(self) -> None:
        """Discover OAuth 2.0 configuration using well-known endpoints."""
        parsed_url = urlparse(self.endpoint)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

        try:
            # Step 1: Try to discover protected resource metadata (RFC 9728)
            protected_resource_url = f"{base_url}/.well-known/oauth-protected-resource"
            try:
                async with self.session.get(protected_resource_url) as response:
                    if response.status == 200:
                        self.protected_resource_metadata = await response.json()
            except Exception:
                # Protected resource metadata is optional
                pass

            # Step 2: Discover authorization server metadata (RFC 8414)
            auth_server_url = f"{base_url}/.well-known/oauth-authorization-server"
            try:
                async with self.session.get(auth_server_url) as response:
                    if response.status == 200:
                        self.authorization_server_metadata = await response.json()
            except Exception:
                # Authorization server metadata discovery failed
                pass

        except Exception:
            # OAuth discovery is optional, continue without it
            pass

    async def _attempt_dynamic_client_registration(self) -> str | None:
        """Attempt OAuth 2.0 Dynamic Client Registration (RFC 7591)."""
        if not self.authorization_server_metadata:
            return None

        registration_endpoint = self.authorization_server_metadata.get("registration_endpoint")
        if not registration_endpoint:
            return None

        # Prepare client registration request
        registration_data = {
            "client_name": "MCP Validation Tool",
            "client_uri": "https://github.com/anthropics/claude-code",
            "redirect_uris": [],  # No redirect needed for client credentials flow
            "grant_types": ["client_credentials"],
            "token_endpoint_auth_method": "client_secret_basic",
            "application_type": "native"
        }

        try:
            async with self.session.post(
                registration_endpoint,
                json=registration_data,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 201:
                    registration_response = await response.json()
                    self.client_id = registration_response.get("client_id")
                    self.client_secret = registration_response.get("client_secret")
                    return registration_response.get("client_id")
        except Exception:
            pass

        return None

    async def _obtain_access_token(self) -> str | None:
        """Obtain OAuth 2.0 access token using client credentials flow."""
        if not self.authorization_server_metadata or not self.client_id or not self.client_secret:
            return None

        token_endpoint = self.authorization_server_metadata.get("token_endpoint")
        if not token_endpoint:
            return None

        # Prepare token request using client credentials flow
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        # Add resource parameter if available (RFC 8707)
        if self.protected_resource_metadata:
            resource = self.protected_resource_metadata.get("resource")
            if resource:
                token_data["resource"] = resource

        try:
            async with self.session.post(
                token_endpoint,
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            ) as response:
                if response.status == 200:
                    token_response = await response.json()
                    return token_response.get("access_token")
        except Exception:
            pass

        return None

    async def _post_request(self, method: str, params: dict[str, Any]) -> aiohttp.ClientResponse:
        """Send POST request with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"
        }

        # Add OAuth Bearer token if available
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

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
            raise ValueError(f"Failed to connect to {self.endpoint}: {e}") from e

        # Handle session ID from response headers
        if "Mcp-Session-Id" in response.headers and not self.session_id:
            self.session_id = response.headers["Mcp-Session-Id"]

        return response

    async def _post_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send POST notification with proper headers."""
        headers = {
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18"
        }

        # Add OAuth Bearer token if available
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

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
            raise ValueError(f"Failed to send notification to {self.endpoint}: {e}") from e

        # Expect 202 Accepted for notifications
        if response.status != 202:
            error_text = await response.text()
            raise ValueError(f"Unexpected response status for notification: {response.status} - {error_text}")

    async def _handle_response(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Handle HTTP response, supporting both JSON and SSE."""
        if response.content_type == "text/event-stream":
            # Handle SSE stream
            return await self._handle_sse_response(response)
        else:
            # Handle regular JSON response
            return await response.json()

    async def _handle_sse_response(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        """Handle SSE stream response."""
        async for line in response.content:
            line_str = line.decode().strip()
            if line_str.startswith("data: "):
                data = line_str[6:].strip()
                if data:
                    return json.loads(data)

        raise ValueError("No data received from SSE stream")

    async def send_request(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC request."""
        if not self._initialized:
            await self.initialize()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"
        }

        # Add OAuth Bearer token if available
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

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

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification."""
        if not self._initialized:
            await self.initialize()

        await self._post_notification(method, params)

    async def send_and_receive(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0
    ) -> dict[str, Any]:
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

    async def read_response(self, timeout: float = 5.0) -> dict[str, Any]:
        """Read and parse a response."""
        # For HTTP transport, this is typically called after send_request
        # In HTTP, we don't have a separate read step like stdio
        raise NotImplementedError("HTTP transport uses send_and_receive instead of separate read_response")

    def parse_response(self, response_line: str) -> dict[str, Any]:
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
                # Give aiohttp time to close connections properly
                await asyncio.sleep(0.1)
            except Exception:
                # Ignore errors during session close
                pass

        self.session = None
        self.session_id = None
        self._initialized = False
