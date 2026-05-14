
#!/usr/bin/env python

"""Echo server using the asyncio API."""

import asyncio
from websockets.asyncio.server import serve


async def echo(websocket):
    async for message in websocket:
        print(message)
        await websocket.send("Received!")


async def main():
    async with serve(echo, "", 8765) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())