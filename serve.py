
#!/usr/bin/env python

"""Echo server using the asyncio API."""

import asyncio
from websockets.asyncio.server import serve
from pathlib import Path
import json

SAVE_DIR = Path("./fax_tests")  # or wherever you want to store them
SAVE_DIR.mkdir(parents=True, exist_ok=True)

async def handler(websocket):
    metadata = None
    async for message in websocket:
        
        if(message == "Hello Server!"):
            await websocket.send("Hello Client!")
            continue
        
        # if string -> it means it's a json -> get data type which should come on next message
        if isinstance(message, str):
            data = json.loads(message)
            if data['type'] == 'fax':
                metadata = data
                print(f"Incoming fax: {metadata['filename']}")
            if data['type'] == 'call':
                metadata = data
                print(f"Incoming call: {metadata['filename']}")
        elif isinstance(message, bytes):
            if metadata is None: 
                print("Error: received binary without metadata")
                await websocket.send(json.dumps({
                    "status": "ERROR",
                    "type": "metadata missing for binary"
                }))
                continue
            
            save_path = SAVE_DIR / metadata['filename']
            save_path.write_bytes(message)
            print(f"Saved: {save_path}")

            # Acknowledge back to Electron
            await websocket.send(json.dumps({
                "status": "ok",
                "filename": metadata['filename'],
                "path": str(save_path)
            }))
            
            metadata = None  # reset for next file
        # await websocket.send("Received!")


async def main():
    async with serve(handler, "", 8765) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())