"""Run the browser app and authenticated Streamable HTTP MCP on one origin."""

from urllib.parse import urlsplit

from a2wsgi import WSGIMiddleware
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from app import create_app
from mcp_server import create_mcp


web_app = create_app()
oauth = web_app.extensions["oauth_state"]
base_url = web_app.config["PUBLIC_BASE_URL"]
resource_url = base_url + "/mcp"
host = urlsplit(base_url).netloc
hostname = urlsplit(base_url).hostname

auth_settings = AuthSettings(
    issuer_url=base_url,
    resource_server_url=resource_url,
    validate_token_resource=True,
    required_scopes=["images:manage"],
    client_registration_options=ClientRegistrationOptions(
        enabled=True,
        valid_scopes=["images:manage", "offline_access"],
        default_scopes=["images:manage", "offline_access"],
    ),
    revocation_options=RevocationOptions(enabled=True),
)

mcp_core = MCPServer(
    "davallka-images",
    auth_server_provider=oauth,
    auth=auth_settings,
)
mcp = create_mcp(server=mcp_core)
mcp_http = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    max_request_body_size=16 * 1024**2,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, hostname, f"{hostname}:443"],
        allowed_origins=[base_url],
    ),
)

MCP_ROUTES = {
    "/mcp",
    "/authorize",
    "/token",
    "/register",
    "/revoke",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource/mcp",
}


class CombinedApplication:
    """Dispatch SDK protocol routes to ASGI and the existing UI to WSGI."""

    def __init__(self, protocol_app, browser_app):
        self.protocol_app = protocol_app
        self.browser_app = browser_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.protocol_app(scope, receive, send)
        elif scope["type"] == "http" and scope["path"] in MCP_ROUTES:
            await self.protocol_app(scope, receive, send)
        else:
            await self.browser_app(scope, receive, send)


app = CombinedApplication(mcp_http, WSGIMiddleware(web_app, workers=8))
