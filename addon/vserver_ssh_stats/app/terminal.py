import asyncio
import json

import asyncssh
import websockets


async def _bridge(websocket: websockets.WebSocketServerProtocol):
    """Bridge between a websocket and an SSH session.

    The first message from the client must be a JSON object containing
    `host`, `user`, optional `password`, and optional `port` (default 22).
    """

    try:
        params_raw = await websocket.recv()
        params = json.loads(params_raw)
    except Exception:
        await websocket.send("Invalid handshake data\n")
        await websocket.close()
        return

    host = params.get("host")
    user = params.get("user")
    password = params.get("password")
    port = int(params.get("port", 22))

    if not host or not user:
        await websocket.send("Missing host or user parameters\n")
        await websocket.close()
        return

    try:
        async with asyncssh.connect(
            host,
            username=user,
            password=password or None,
            known_hosts=None,
            port=port,
        ) as conn:
            async with conn.create_process(term_type="xterm") as process:
                async def ws_to_ssh():
                    async for message in websocket:
                        process.stdin.write(message)

                async def ssh_to_ws():
                    async for data in process.stdout:
                        await websocket.send(data)

                await asyncio.gather(ws_to_ssh(), ssh_to_ws())
    except Exception as exc:  # pragma: no cover - network errors
        await websocket.send(f"Connection failed: {exc}\n")


async def main() -> None:
    """Run the websocket SSH bridge."""
    async with websockets.serve(_bridge, "0.0.0.0", 8098):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
