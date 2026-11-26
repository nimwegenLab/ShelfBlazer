"""The MCP endpoint: `/mcp` inside the app (Model Context Protocol over streamable HTTP, JSON answers, no server-side sessions), so a
person's own Claude Code can read and manage their records with their own rights. Off unless ``SHELFBLAZER_MCP=1`` (docs/design.md,
section 18). The tools themselves are in ``mcp_tools``.

Every request carries ``Authorization: Bearer <personal access token>`` (created on the account page). The gateway looks the token up
(``Users.authenticate_token``: hash lookup, expiry, active account; a sender of wrong tokens is throttled), checks that the token's
scope may use this endpoint, and runs the tool as the token owner with ``mcp_tools.member`` rights. ``/mcp`` offers the member tools;
administrator tools, when there are any, are on ``/mcp/admin`` and need an administrator token, so a member's agent never sees them.
"""
import contextlib, contextvars, functools, inspect, json

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from . import mcp_tools as tools

INSTRUCTIONS = {
    "member": ("ShelfBlazer is this group's inventory of lab records. You act as the person who connected you, with their rights, and "
               "every change is committed under their name. Call list_types first. Before writing a record of a type, read the resource "
               "conventions://<type>. Call check_record first and resolve each finding, or ask the person about it; never create a "
               "record the check reports as a possible duplicate without the person's confirmation (a repeated stock of the same "
               "strain is legitimate, a second entry for the same tube is not). Show the preview of import_table to the person before "
               "applying it. Records are archived, never deleted. What you read here is sent to the person's Claude account: do not "
               "put anything confidential into records."),
    "admin": "ShelfBlazer administrator tools. Every change that is hard to undo has a preview call and an apply call: show the preview first.",
}

_actor = contextvars.ContextVar("shelfblazer_mcp_actor")


def _bind(env, fn, name, description):
    """A function the SDK can describe: ``fn`` without its two leading parameters (the environment and the actor, which the gateway sets)."""
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())[2:]

    async def call(**kw):
        return fn(env, _actor.get(), **kw)
    call.__name__, call.__doc__ = name, description
    call.__signature__ = sig.replace(parameters=params)
    call.__annotations__ = {p.name: p.annotation for p in params if p.annotation is not inspect.Parameter.empty}
    return call


def build(env, scope):
    """The MCP server of one endpoint: the tools registered for exactly this scope, plus the resources (member endpoint only)."""
    server = MCPServer("ShelfBlazer", instructions=INSTRUCTIONS[scope])
    for name, t in tools.TOOLS.items():
        if t.scope == scope:
            server.add_tool(_bind(env, t.fn, name, t.description), name=name, description=t.description)
    if scope == "member":
        def conventions(type: str) -> str:
            return tools.conventions(env, _actor.get(), type)
        def schema(type: str) -> str:
            return json.dumps(tools.schema(env, _actor.get(), type), indent=1)
        server.resource("conventions://{type}", name="conventions", mime_type="text/markdown",
                        description="How this group writes records of a type: naming, what goes in which field. Read before writing.")(conventions)
        server.resource("schema://{type}", name="schema", mime_type="application/json",
                        description="The fields of a record type, with options and what each link points to.")(schema)
    return server


class Gateway:
    """ASGI app in front of one MCP server: bearer authentication, scope check, then the request runs as the token owner."""

    def __init__(self, users, scope, path, mcp_app):
        self.users, self.scope, self.path, self.mcp_app = users, scope, path, mcp_app

    async def refuse(self, send, status, message):
        body = json.dumps({"error": message}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="shelfblazer"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        if scope["path"].rstrip("/") != self.path:
            return await self.refuse(send, 404, "not found")
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        ip = scope["client"][0] if scope.get("client") else "?"
        user, rec = self.users.authenticate_token(token, ip)
        if not user:
            if self.users.token_locked(ip):
                return await self.refuse(send, 429, "Too many attempts. Wait a few minutes.")
            return await self.refuse(send, 401, "Missing, wrong, expired or revoked access token.")
        if not tools.allowed(rec["scope"], self.scope):
            return await self.refuse(send, 403, "This token may not use this endpoint.")
        reset = _actor.set(tools.member(self.users.public(user)))
        try:
            await self.mcp_app(dict(scope, path="/", raw_path=b"/", root_path=""), receive, send)
        finally:
            _actor.reset(reset)


class Mcp:
    """The enabled endpoints of one app: build the servers, mount them, run their session managers with the app."""

    def __init__(self, store, index, users):
        env = tools.environment(store, index, users)
        self.users = users
        self.endpoints = {}                       # path -> Gateway
        for scope, path in (("member", "/mcp"), ("admin", "/mcp/admin")):
            if scope == "member" or any(t.scope == scope for t in tools.TOOLS.values()):
                server = build(env, scope)
                mcp_app = server.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                                     transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
                self.endpoints[path] = (Gateway(users, scope, path, mcp_app), mcp_app)

    @contextlib.asynccontextmanager
    async def lifespan(self, app):
        async with contextlib.AsyncExitStack() as stack:
            for _, mcp_app in self.endpoints.values():
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield

    def mount(self, app):
        from starlette.routing import Mount, Route
        for path, (gateway, _) in sorted(self.endpoints.items(), key=lambda kv: -len(kv[0])):      # /mcp/admin before /mcp
            app.router.routes.append(Route(path, gateway))                 # no redirect from /mcp to /mcp/ (POST bodies)
            app.router.routes.append(Mount(path, gateway))
