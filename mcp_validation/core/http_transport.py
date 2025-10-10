"""HTTP transport implementation for MCP communication."""

import asyncio
import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from ..utils.debug import verbose_log
from .transport import MCPTransport


class SimpleTokenStorage(TokenStorage):
    """Simple in-memory token storage for OAuth tokens following MCP interface."""

    def __init__(self):
        self._tokens: OAuthToken | None = None
        self._client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        """Get stored OAuth tokens."""
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        """Store OAuth tokens."""
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        """Get stored client information from dynamic registration."""
        return self._client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Store client information from dynamic registration."""
        self._client_info = client_info

    # Legacy methods for backward compatibility
    async def get_token(self, token_type: str) -> str | None:
        """Get stored token by type (legacy method)."""
        if self._tokens:
            if token_type == "access_token":
                return self._tokens.access_token
            elif token_type == "refresh_token":
                return self._tokens.refresh_token
        return None

    async def store_token(self, token_type: str, token: str) -> None:
        """Store token by type (legacy method)."""
        if not self._tokens:
            if token_type == "access_token":
                self._tokens = OAuthToken(access_token=token)
        else:
            if token_type == "access_token":
                # Create new token object with updated access token
                self._tokens = OAuthToken(
                    access_token=token,
                    refresh_token=self._tokens.refresh_token,
                    expires_in=self._tokens.expires_in,
                    token_type=self._tokens.token_type,
                )

    async def clear_tokens(self) -> None:
        """Clear all stored tokens."""
        self._tokens = None
        self._client_info = None


class HTTPTransport(MCPTransport):
    """HTTP-based MCP transport using MCP SDK's streamablehttp_client with OAuth 2.0 support."""

    def __init__(
        self,
        endpoint: str,
        auth_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ):
        self.endpoint = endpoint
        self.auth_token = auth_token
        self.client_id = client_id
        self.client_secret = client_secret

        # MCP SDK transport streams and session
        self.read_stream = None
        self.write_stream = None
        self.get_session_id = None
        self._connection_context = None
        self._client_session: ClientSession | None = None
        self._session_context = None
        self._initialized = False
        self._server_info: dict[str, Any] | None = None
        self._init_result = None

        # Validate endpoint URL
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Invalid endpoint URL: {endpoint}")

    def _create_oauth_provider(self) -> OAuthClientProvider | None:
        """Create OAuth provider based on available credentials."""

        # If we have a pre-existing auth_token, create a provider with pre-populated tokens
        if self.auth_token:
            verbose_log(f"🔑 Using provided auth token: {self.auth_token[:10]}...")
            return self._create_token_oauth_provider()

        # Try different OAuth strategies based on available credentials
        if self.client_id and self.client_secret:
            verbose_log("🔑 Using pre-registered client credentials")
            return self._create_pre_registered_oauth_provider()
        else:
            verbose_log(
                "🔧 No static OAuth credentials provided, attempting dynamic client registration"
            )
            return self._create_dynamic_oauth_provider()

    def _create_token_oauth_provider(self) -> OAuthClientProvider | None:
        """Create OAuth provider with pre-existing access token."""
        try:
            # Create token storage with pre-existing token
            token_storage = SimpleTokenStorage()

            # Create an OAuthToken with the provided access token
            oauth_token = OAuthToken(
                access_token=self.auth_token,
                token_type="Bearer",
                expires_in=3600,  # Default 1 hour expiry
                refresh_token=None,
            )

            # Pre-populate the token storage with the provided token
            # We'll store it directly in the private field for synchronous access
            token_storage._tokens = oauth_token

            # Create minimal client metadata for token-based auth
            client_metadata_dict = {"scope": "mcp"}

            verbose_log(f"📋 Token-based auth metadata: {client_metadata_dict}")
            client_metadata = OAuthClientMetadata.model_validate(client_metadata_dict)

            # Extract base server URL for OAuth (remove MCP-specific path)
            parsed_endpoint = urlparse(self.endpoint)
            oauth_server_url = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"
            verbose_log(f"🌐 OAuth server URL: {oauth_server_url}")
            verbose_log(f"🎯 MCP endpoint: {self.endpoint}")

            # Dummy callback handlers since we already have a token
            async def redirect_handler(url: str) -> None:
                verbose_log(
                    f"🔄 OAuth redirect handler called (should not happen with token): {url}"
                )

            async def callback_handler() -> tuple[str, str | None]:
                verbose_log("📞 OAuth callback handler called (should not happen with token)")
                raise Exception("Callback should not be needed when using access token")

            # Create OAuth provider with token
            verbose_log("🏗️ Creating OAuth provider with access token...")
            oauth_provider = OAuthClientProvider(
                server_url=oauth_server_url,
                client_metadata=client_metadata,
                storage=token_storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            verbose_log("✅ OAuth provider created with access token")
            return oauth_provider

        except Exception as e:
            verbose_log(f"❌ Token OAuth provider setup failed: {e}")
            return None

    def _create_pre_registered_oauth_provider(self) -> OAuthClientProvider | None:
        """Create OAuth provider for pre-registered client credentials."""
        try:
            # Create token storage
            token_storage = SimpleTokenStorage()

            # Create client metadata for pre-registered clients
            client_metadata_dict = {
                "client_name": "MCP Validation Tool",
                "client_uri": "https://github.com/modelcontextprotocol/mcp-validation",
                "redirect_uris": ["http://localhost:3333/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
                "scope": "mcp api read_user",  # GitLab requires these scopes
            }

            verbose_log(f"📋 Pre-registered client metadata: {client_metadata_dict}")
            client_metadata = OAuthClientMetadata.model_validate(client_metadata_dict)

            # Extract base server URL for OAuth (remove MCP-specific path)
            parsed_endpoint = urlparse(self.endpoint)
            oauth_server_url = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"
            verbose_log(f"🌐 OAuth server URL: {oauth_server_url}")
            verbose_log(f"🎯 MCP endpoint: {self.endpoint}")

            # Callback handlers that inform the user about the OAuth flow
            async def redirect_handler(url: str) -> None:
                verbose_log(f"🔄 OAuth redirect to: {url}")
                print("\n🌐 OAuth Authentication Required")
                print("Opening browser for authentication...")

                # Automatically open the browser like the MCP SDK simple-auth-client example
                import webbrowser

                try:
                    webbrowser.open(url)
                    print("✅ Browser opened successfully")
                    print("Please complete the authentication in your browser")
                except Exception as e:
                    verbose_log(f"Failed to open browser: {e}")
                    print("❌ Could not open browser automatically")
                    print("Please manually open this URL in your browser:")
                    print(f"{url}")

                print("Waiting for authentication...")

            async def callback_handler() -> tuple[str, str | None]:
                verbose_log("📞 OAuth callback handler called")
                print("⏳ Waiting for OAuth callback...")
                # Implement OAuth callback server like mcp_simple_auth_client
                return await self._start_oauth_callback_server()

            # Create OAuth provider with pre-registered client
            verbose_log("🏗️ Creating OAuth provider for pre-registered client...")
            oauth_provider = OAuthClientProvider(
                server_url=oauth_server_url,
                client_metadata=client_metadata,
                storage=token_storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            verbose_log("✅ OAuth provider created for pre-registered client")
            return oauth_provider

        except Exception as e:
            verbose_log(f"❌ Pre-registered OAuth provider setup failed: {e}")
            return None

    def _create_minimal_oauth_provider(self) -> OAuthClientProvider | None:
        """Create minimal OAuth provider like mcp-remote for dynamic authentication."""
        try:
            # Create token storage
            token_storage = SimpleTokenStorage()

            # Create minimal client metadata like mcp-remote
            client_metadata_dict = {"scope": "mcp"}  # Just the MCP scope like mcp-remote

            verbose_log(f"📋 Minimal client metadata: {client_metadata_dict}")
            client_metadata = OAuthClientMetadata.model_validate(client_metadata_dict)

            # Extract base server URL for OAuth (remove MCP-specific path)
            parsed_endpoint = urlparse(self.endpoint)
            oauth_server_url = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"
            verbose_log(f"🌐 OAuth server URL: {oauth_server_url}")
            verbose_log(f"🎯 MCP endpoint: {self.endpoint}")

            # Simple callback handlers for potential future use
            async def redirect_handler(url: str) -> None:
                verbose_log(f"🔄 OAuth redirect handler called with URL: {url}")
                # For validation tool, we don't open browser automatically
                verbose_log(
                    "⚠️ OAuth requires browser authentication - not supported in validation mode"
                )

            async def callback_handler() -> tuple[str, str | None]:
                verbose_log("📞 OAuth callback handler called")
                # For validation tool, we can't wait for browser callback
                raise Exception("Browser OAuth callback not supported in validation mode")

            # Create minimal OAuth provider
            verbose_log("🏗️ Creating minimal OAuth provider...")
            oauth_provider = OAuthClientProvider(
                server_url=oauth_server_url,
                client_metadata=client_metadata,
                storage=token_storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            verbose_log("✅ Minimal OAuth provider created")
            return oauth_provider

        except Exception as e:
            verbose_log(f"❌ Minimal OAuth provider setup failed: {e}")
            return None

    def _create_dynamic_oauth_provider(self) -> OAuthClientProvider | None:
        """Create OAuth provider with dynamic client registration like mcp-remote."""
        try:
            # Create token storage
            token_storage = SimpleTokenStorage()

            # Create minimal client metadata like mcp-remote for dynamic registration
            # Based on mcp-remote's --static-oauth-client-metadata '{"scope": "mcp"}' pattern
            # But we need to satisfy MCP SDK's requirements for required fields
            client_metadata_dict = {
                "client_name": "mcp-validate",  # Short name to avoid GitLab's length restriction
                "redirect_uris": ["http://localhost:3333/callback"],  # Required by MCP SDK
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",  # No client secret for dynamic registration
                "scope": "mcp",  # The key scope like mcp-remote
            }

            verbose_log(f"📋 Dynamic registration metadata (minimal): {client_metadata_dict}")
            client_metadata = OAuthClientMetadata.model_validate(client_metadata_dict)

            # Extract base server URL for OAuth (remove MCP-specific path)
            parsed_endpoint = urlparse(self.endpoint)
            oauth_server_url = f"{parsed_endpoint.scheme}://{parsed_endpoint.netloc}"
            verbose_log(f"🌐 OAuth server URL: {oauth_server_url}")
            verbose_log(f"🎯 MCP endpoint: {self.endpoint}")

            # Create callback handlers that inform the user about the OAuth flow
            async def redirect_handler(url: str) -> None:
                verbose_log(f"🔄 OAuth redirect to: {url}")
                print("\n🌐 OAuth Authentication Required")
                print("Opening browser for authentication...")

                # Automatically open the browser like the MCP SDK simple-auth-client example
                import webbrowser

                try:
                    webbrowser.open(url)
                    print("✅ Browser opened successfully")
                    print("Please complete the authentication in your browser")
                except Exception as e:
                    verbose_log(f"Failed to open browser: {e}")
                    print("❌ Could not open browser automatically")
                    print("Please manually open this URL in your browser:")
                    print(f"{url}")

                print("Waiting for authentication...")

            async def callback_handler() -> tuple[str, str | None]:
                verbose_log("📞 OAuth callback handler called")
                print("⏳ Waiting for OAuth callback...")
                # Implement OAuth callback server like mcp_simple_auth_client
                return await self._start_oauth_callback_server()

            # Create OAuth provider with dynamic registration capability
            verbose_log("🏗️ Creating OAuth provider with dynamic registration (minimal)...")
            oauth_provider = OAuthClientProvider(
                server_url=oauth_server_url,
                client_metadata=client_metadata,
                storage=token_storage,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
            verbose_log("✅ OAuth provider created for dynamic registration (minimal)")
            return oauth_provider

        except Exception as e:
            verbose_log(f"❌ Dynamic OAuth provider setup failed: {e}")
            return None

    async def _check_authentication(self) -> None:
        """Pre-flight check to detect authentication issues before MCP SDK initialization."""
        verbose_log("🔍 Performing pre-flight authentication check...")

        # Skip pre-flight check if OAuth credentials are provided OR if no credentials at all
        # OAuth (both pre-registered and dynamic registration) requires full browser flow which can't be tested in pre-flight
        if self.client_id and self.client_secret:
            verbose_log(
                "🔧 OAuth credentials provided, skipping pre-flight check (OAuth requires browser flow)"
            )
            return

        if not self.auth_token and not self.client_id and not self.client_secret:
            verbose_log(
                "🔧 No credentials provided, will attempt dynamic OAuth registration (skipping pre-flight check)"
            )
            return

        try:
            # Create a simple HTTP client to test the endpoint
            async with httpx.AsyncClient() as client:
                # Send a simple POST request to check authentication
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "MCP-Protocol-Version": "2025-06-18",
                }

                # Add auth token if available
                if self.auth_token:
                    headers["Authorization"] = f"Bearer {self.auth_token}"
                    verbose_log(
                        f"🔑 Using auth token for pre-flight check: {self.auth_token[:10]}..."
                    )

                # Simple test request (this will likely fail, but we want to see HOW it fails)
                test_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp-validate-test", "version": "1.0.0"},
                    },
                }

                verbose_log("📤 Sending pre-flight test request...")
                response = await client.post(
                    self.endpoint, json=test_request, headers=headers, timeout=10.0
                )

                verbose_log(f"📥 Pre-flight response: {response.status_code}")

                # Log response headers for debugging
                if response.status_code in [401, 403]:
                    verbose_log(f"🔍 Response headers: {dict(response.headers)}")
                    if response.text:
                        verbose_log(f"🔍 Response body: {response.text[:200]}...")

                if response.status_code == 401:
                    verbose_log("❌ Pre-flight check: 401 Unauthorized")

                    # Provide specific guidance for GitLab
                    if "gitlab.com" in self.endpoint.lower():
                        raise ValueError(
                            f"GitLab MCP endpoint requires authentication. To use GitLab's MCP:\n"
                            f"1. Create a GitLab OAuth application at: https://gitlab.com/-/profile/applications\n"
                            f"2. Use scopes: 'api read_user'\n"
                            f"3. Set redirect URI: http://localhost:3333/callback\n"
                            f"4. Run: mcp-validate --transport http --endpoint {self.endpoint} --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET\n"
                            f"OR obtain a personal access token and use: --auth-token YOUR_TOKEN"
                        )
                    else:
                        raise ValueError(
                            f"Authentication required for {self.endpoint}. "
                            f"The server requires valid OAuth credentials. "
                            f"Please provide --client-id and --client-secret, or use --auth-token."
                        )
                elif response.status_code == 403:
                    verbose_log("❌ Pre-flight check: 403 Forbidden")
                    raise ValueError(
                        f"Access forbidden for {self.endpoint}. "
                        f"Please check your OAuth scopes and permissions."
                    )
                elif response.status_code >= 500:
                    verbose_log(f"⚠️ Pre-flight check: {response.status_code} Server Error")
                    # Server errors are not authentication issues, let MCP SDK handle them
                    verbose_log("✅ Pre-flight check passed (server available)")
                else:
                    verbose_log("✅ Pre-flight check passed (endpoint accessible)")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                verbose_log("❌ Pre-flight check failed: 401 Unauthorized")
                raise ValueError(
                    f"Authentication required for {self.endpoint}. "
                    f"Please provide OAuth credentials using --client-id and --client-secret, "
                    f"or use --auth-token with a valid access token."
                ) from e
            elif e.response.status_code == 403:
                verbose_log("❌ Pre-flight check failed: 403 Forbidden")
                raise ValueError(
                    f"Access forbidden for {self.endpoint}. "
                    f"Please check your OAuth scopes and permissions."
                ) from e
            else:
                # Other HTTP errors are not necessarily authentication issues
                verbose_log(f"⚠️ Pre-flight check: HTTP {e.response.status_code}")

        except ValueError as e:
            # Re-raise authentication errors from our own checks
            verbose_log(f"⚠️ Pre-flight check failed with: {e}")
            raise
        except Exception as e:
            verbose_log(f"⚠️ Pre-flight check failed with: {e}")
            # Don't fail on connection errors, let MCP SDK handle them
            if "401" in str(e) or "Unauthorized" in str(e):
                raise ValueError(
                    f"Authentication required for {self.endpoint}. "
                    f"Please provide OAuth credentials."
                ) from e

    async def initialize(self) -> None:
        """Initialize HTTP connection using MCP SDK's streamablehttp_client."""
        if self._initialized:
            return

        verbose_log(f"🔗 Initializing HTTP transport to {self.endpoint}")

        # Pre-flight authentication check to avoid MCP SDK crashes
        await self._check_authentication()

        # Create OAuth provider if needed
        oauth_provider = self._create_oauth_provider()

        try:
            # Use MCP SDK's streamablehttp_client
            verbose_log("📡 Opening StreamableHTTP connection...")
            self._connection_context = streamablehttp_client(
                url=self.endpoint,
                auth=oauth_provider,
                timeout=timedelta(seconds=60),
            )

            # Enter the context and get streams
            try:
                streams = await self._connection_context.__aenter__()
                self.read_stream, self.write_stream, self.get_session_id = streams
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

                # Handle HTTP connection errors (like 401 Unauthorized)
                if "401" in str(connection_error) or "Unauthorized" in str(connection_error):
                    verbose_log("❌ HTTP 401 Unauthorized - authentication required")
                    raise ValueError(
                        f"Authentication required for {self.endpoint}. "
                        f"Please provide OAuth credentials using --client-id and --client-secret, "
                        f"or use --auth-token with a valid access token."
                    ) from connection_error
                elif "403" in str(connection_error) or "Forbidden" in str(connection_error):
                    verbose_log("❌ HTTP 403 Forbidden - insufficient permissions")
                    raise ValueError(
                        f"Access forbidden for {self.endpoint}. "
                        f"Please check your OAuth scopes and permissions."
                    ) from connection_error
                else:
                    verbose_log(f"❌ HTTP connection failed: {connection_error}")
                    raise ValueError(
                        f"Failed to connect to {self.endpoint}: {connection_error}"
                    ) from connection_error

            # Create and initialize ClientSession for protocol communication
            verbose_log("🤝 Creating MCP client session...")
            self._session_context = ClientSession(self.read_stream, self.write_stream)
            self._client_session = await self._session_context.__aenter__()

            verbose_log("⚡ Initializing MCP session...")
            try:
                init_result = await self._client_session.initialize()
                # Store the initialize result to extract serverInfo
                self._init_result = init_result
                if init_result and hasattr(init_result, "serverInfo"):
                    self._server_info = {
                        "name": init_result.serverInfo.name,
                        "version": init_result.serverInfo.version,
                    }
                    verbose_log(
                        f"📋 Server info: {self._server_info['name']} v{self._server_info['version']}"
                    )
            except Exception as session_error:
                # Handle MCP session initialization errors (like authentication failures)
                if "401" in str(session_error) or "Unauthorized" in str(session_error):
                    verbose_log("❌ MCP session initialization failed: 401 Unauthorized")
                    raise ValueError(
                        f"Authentication required for MCP endpoint {self.endpoint}. "
                        f"The server requires valid OAuth credentials. "
                        f"Please provide --client-id and --client-secret, or use --auth-token."
                    ) from session_error
                elif "403" in str(session_error) or "Forbidden" in str(session_error):
                    verbose_log("❌ MCP session initialization failed: 403 Forbidden")
                    raise ValueError(
                        f"Access forbidden for MCP endpoint {self.endpoint}. "
                        f"Please check your OAuth scopes and permissions."
                    ) from session_error
                else:
                    verbose_log(f"❌ MCP session initialization failed: {session_error}")
                    raise ValueError(
                        f"Failed to initialize MCP session: {session_error}"
                    ) from session_error

            verbose_log("✅ HTTP transport and MCP session initialized successfully")
            self._initialized = True

        except Exception as e:
            verbose_log(f"❌ Failed to initialize HTTP transport: {e}")

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

            raise ValueError(f"Failed to initialize HTTP transport: {e}") from e

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
                # Return successful initialization response in JSON-RPC format with real serverInfo
                verbose_log("✅ Initialize request - session already initialized")

                # Build response from stored init_result
                result_data = {
                    "protocolVersion": (
                        self._init_result.protocolVersion if self._init_result else "2025-06-18"
                    ),
                    "capabilities": {},
                    "serverInfo": self._server_info
                    or {"name": "HTTP MCP Server", "version": "unknown"},
                }

                # Add capabilities if available
                if self._init_result and hasattr(self._init_result, "capabilities"):
                    caps = self._init_result.capabilities
                    result_data["capabilities"] = {
                        "tools": caps.tools.model_dump() if hasattr(caps, "tools") and caps.tools else {},
                        "resources": (
                            caps.resources.model_dump()
                            if hasattr(caps, "resources") and caps.resources
                            else {}
                        ),
                        "prompts": (
                            caps.prompts.model_dump() if hasattr(caps, "prompts") and caps.prompts else {}
                        ),
                        "logging": (
                            caps.logging.model_dump() if hasattr(caps, "logging") and caps.logging else {}
                        ),
                    }

                return {"jsonrpc": "2.0", "id": 1, "result": result_data}
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
                        "message": f"Method {method} not supported in HTTP transport",
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
        response_text = await asyncio.wait_for(self.read_stream.receive(), timeout=timeout)
        return json.loads(response_text)

    def parse_response(self, response_line: str) -> dict[str, Any]:
        """Parse a response line."""
        try:
            return json.loads(response_line.strip())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response: {e}") from e

    async def close(self) -> None:
        """Close the transport connection."""
        verbose_log("🔄 Closing HTTP transport...")

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

        # Close HTTP connection context
        if self._connection_context:
            try:
                await self._connection_context.__aexit__(None, None, None)
                verbose_log("✅ HTTP transport closed successfully")
            except Exception as e:
                verbose_log(f"⚠️ Error during HTTP transport cleanup: {e}")
            finally:
                self._connection_context = None

        self.read_stream = None
        self.write_stream = None
        self.get_session_id = None
        self._initialized = False

    async def _start_oauth_callback_server(self) -> tuple[str, str | None]:
        """Start OAuth callback server and wait for authorization code, like mcp_simple_auth_client."""
        # Create a callback server to handle OAuth redirect
        callback_data = {"authorization_code": None, "state": None, "error": None}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                query_params = parse_qs(parsed.query)

                if "code" in query_params:
                    callback_data["authorization_code"] = query_params["code"][0]
                    callback_data["state"] = query_params.get("state", [None])[0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"""
                    <html>
                    <body>
                        <h1>Authorization Successful!</h1>
                        <p>You can close this window and return to the terminal.</p>
                        <script>setTimeout(() => window.close(), 2000);</script>
                    </body>
                    </html>
                    """
                    )
                elif "error" in query_params:
                    callback_data["error"] = query_params["error"][0]
                    self.send_response(400)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        f"""
                    <html>
                    <body>
                        <h1>Authorization Failed</h1>
                        <p>Error: {query_params["error"][0]}</p>
                        <p>You can close this window and return to the terminal.</p>
                    </body>
                    </html>
                    """.encode()
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                # Suppress HTTP server logging
                pass

        # Start callback server on port 3333 (matching redirect URI)
        server = HTTPServer(("localhost", 3333), CallbackHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        verbose_log("🖥️ Started OAuth callback server on http://localhost:3333")
        print("🖥️ Started callback server on http://localhost:3333")

        try:
            # Wait for OAuth callback with timeout (5 minutes)
            timeout = 300  # 5 minutes
            start_time = time.time()

            while time.time() - start_time < timeout:
                if callback_data["authorization_code"]:
                    verbose_log("✅ Received OAuth callback with authorization code")
                    return callback_data["authorization_code"], callback_data["state"]
                elif callback_data["error"]:
                    error = callback_data["error"]
                    verbose_log(f"❌ OAuth callback error: {error}")
                    raise Exception(f"OAuth error: {error}")

                # Sleep a bit to avoid busy waiting
                await asyncio.sleep(0.1)

            # Timeout reached
            verbose_log("⏰ OAuth callback timeout reached")
            raise Exception("Timeout waiting for OAuth callback")

        finally:
            # Clean up server
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)
            verbose_log("🔄 OAuth callback server stopped")
