"""Pinned OpenClaw protocol 4: authenticated handshake and bounded RPC transport.

No server error body is exposed: gateways may echo credentials in diagnostics.
Capabilities and granted scopes come from hello-ok, never from desired config.
"""

import asyncio
import json
import uuid

import websockets

from .identity import sign_device

CONTROLLER_SCOPES = ("operator.read", "operator.write")


class GatewayError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"OpenClaw gateway: {code}")


class GatewayClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        scopes: tuple[str, ...] = CONTROLLER_SCOPES,
        timeout: float = 15.0,
        device_key: str | None = None,
    ) -> None:
        self._url, self._token = url, token
        self._scopes, self._timeout = scopes, timeout
        self._device_key = device_key
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._failure: GatewayError | None = None
        self.events: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.hello: dict = {}

    async def __aenter__(self) -> "GatewayClient":
        try:
            async with asyncio.timeout(self._timeout):
                self._ws = await websockets.connect(
                    self._url, max_size=25 * 1024 * 1024, close_timeout=2, proxy=None
                )
                challenge = json.loads(await self._ws.recv())
                if challenge.get("event") != "connect.challenge":
                    raise GatewayError("challenge_missing")
                nonce = (challenge.get("payload") or {}).get("nonce")
                if not isinstance(nonce, str) or not nonce or len(nonce) > 1024:
                    raise GatewayError("challenge_invalid")
                device = (
                    {"device": sign_device(self._device_key, nonce, self._token, self._scopes)}
                    if self._device_key
                    else {}
                )
                await self._ws.send(
                    json.dumps(
                        {
                            "type": "req",
                            "id": "connect-0",
                            "method": "connect",
                            "params": {
                                "minProtocol": 4,
                                "maxProtocol": 4,
                                "client": {
                                    "id": "gateway-client",
                                    "displayName": "Eidolon",
                                    "version": "0.2.0",
                                    "platform": "python",
                                    "mode": "backend",
                                },
                                "role": "operator",
                                "scopes": list(self._scopes),
                                "caps": [],
                                "auth": {"token": self._token},
                                **device,
                            },
                        }
                    )
                )
                while True:
                    message = json.loads(await self._ws.recv())
                    if message.get("type") == "res" and message.get("id") == "connect-0":
                        if message.get("ok") is not True:
                            if (message.get("error") or {}).get("code") == "NOT_PAIRED":
                                raise GatewayError("pairing_required")
                            raise GatewayError("authentication_rejected")
                        hello = message.get("payload")
                        if not isinstance(hello, dict) or hello.get("type") != "hello-ok":
                            raise GatewayError("invalid_hello")
                        if hello.get("protocol") != 4:
                            raise GatewayError("protocol_mismatch")
                        granted = (hello.get("auth") or {}).get("scopes", [])
                        if not set(self._scopes).issubset(granted):
                            raise GatewayError("scope_missing")
                        self.hello = hello
                        break
            self._reader = asyncio.create_task(self._read_loop(), name="openclaw-ws-reader")
            return self
        except BaseException:
            if self._ws is not None:
                await self._ws.close()
            raise

    async def __aexit__(self, *exc) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._ws is not None:
            await self._ws.close()

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                if message.get("type") == "res":
                    future = self._pending.pop(message.get("id"), None)
                    if future is not None and not future.done():
                        future.set_result(message)
                elif message.get("type") == "event":
                    self.events.put_nowait(message)
        except asyncio.CancelledError:
            raise
        except asyncio.QueueFull:
            self._failure = GatewayError("event_buffer_full")
        except Exception:
            self._failure = GatewayError("connection_failed")
        finally:
            self._failure = self._failure or GatewayError("connection_closed")
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(self._failure)
            self._pending.clear()

    async def call(self, method: str, params: dict, timeout: float = 60.0):
        if self._failure:
            raise self._failure
        request_id = f"eidolon-{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout):
                await self._ws.send(
                    json.dumps(
                        {"type": "req", "id": request_id, "method": method, "params": params}
                    )
                )
                message = await future
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
        if message.get("ok") is not True:
            raise GatewayError("rpc_rejected")
        return message.get("payload")
