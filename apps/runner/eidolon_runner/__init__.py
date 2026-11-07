"""Eidolon runner: the sandboxed tool executor (v2-architecture §2.2, §4.5, V3).

A separate process per workspace. It holds no provider keys, sees only the
environment variables the client chose to pass, and refuses every path outside
its workspace. Standard library only, so the protocol — JSON-RPC 2.0, one object
per line over stdio — can be reimplemented in Go later without the server noticing.
"""

VERSION = "0.1.0"
PROTOCOL = "eidolon.runner/1"
