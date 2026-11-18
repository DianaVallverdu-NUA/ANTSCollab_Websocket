#!/usr/bin/env python

"""WebSocket server for scene playback and fax-time recalculation."""

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

from websockets.asyncio.server import serve

from scene_runner import FaxController, SceneRunner

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_SCENE_UPLOAD_SIZE = 100 * 1024 * 1024
MAX_SCENE_ASSETS = 256

DEFAULT_DATA_DIR = (
    Path("/fax/websocket_assets")
    if Path("/fax").is_dir()
    else Path(__file__).parent / "fax_tests"
)
DATA_DIR = Path(
    os.environ.get("ANTSCOLLAB_WEBSOCKET_DATA", DEFAULT_DATA_DIR)
).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"

fax_controller = FaxController()
scene_runner = SceneRunner(fax_controller)


def _request_id(value):
    if not isinstance(value, str):
        raise ValueError("A requestId is required.")
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise ValueError("requestId must be a UUID.") from error


def _safe_filename(value, extension=None):
    if not isinstance(value, str) or Path(value).name != value:
        raise ValueError("Invalid asset filename.")
    if value in ("", ".", "..") or "\x00" in value:
        raise ValueError("Invalid asset filename.")
    if extension and Path(value).suffix.lower() not in extension:
        raise ValueError("Invalid TIFF filename.")
    return value


def _asset_descriptors(value):
    if not isinstance(value, list) or len(value) > MAX_SCENE_ASSETS:
        raise ValueError("Invalid scene asset list.")

    descriptors = []
    seen_ids = set()
    total_size = 0
    for descriptor in value:
        if not isinstance(descriptor, dict):
            raise ValueError("Invalid scene asset descriptor.")
        asset_id = _request_id(descriptor.get("id"))
        if asset_id in seen_ids:
            raise ValueError("Duplicate scene asset id.")
        seen_ids.add(asset_id)
        filename = _safe_filename(descriptor.get("filename"))
        size = descriptor.get("size")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > MAX_FILE_SIZE
        ):
            raise ValueError("Invalid scene asset size.")
        total_size += size
        if total_size > MAX_SCENE_UPLOAD_SIZE:
            raise ValueError("Scene assets exceed the total upload limit.")
        descriptors.append({"id": asset_id, "filename": filename, "size": size})
    return descriptors


async def _receive_binary(websocket, descriptor, target):
    message = await websocket.recv()
    if not isinstance(message, bytes):
        raise ValueError("Expected a binary asset after its metadata.")
    if len(message) != descriptor["size"]:
        raise ValueError("Uploaded asset size does not match its metadata.")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(target.write_bytes, message)


async def _receive_scene_assets(websocket, request_dir, descriptors):
    assets = {}
    for descriptor in descriptors:
        asset_dir = request_dir / descriptor["id"]
        target = asset_dir / descriptor["filename"]
        await _receive_binary(websocket, descriptor, target)
        assets[descriptor["id"]] = target
    return assets


async def _send_json(websocket, message):
    await websocket.send(json.dumps(message))


async def _handle_play_scene(websocket, message):
    request_id = _request_id(message.get("requestId"))
    scene = message.get("scene")
    if not isinstance(scene, dict):
        raise ValueError("Invalid scene payload.")
    descriptors = _asset_descriptors(message.get("assets"))
    request_dir = UPLOAD_DIR / request_id

    try:
        assets = await _receive_scene_assets(websocket, request_dir, descriptors)
        runnable_scene = scene_runner.resolve_scene_assets(scene, assets)
        await scene_runner.play(runnable_scene, request_dir)
    except Exception:
        shutil.rmtree(request_dir, ignore_errors=True)
        raise

    await _send_json(
        websocket,
        {"type": "play_scene", "requestId": request_id, "status": "accepted"},
    )


async def _handle_recalculate_time(websocket, message):
    request_id = _request_id(message.get("requestId"))
    filename = _safe_filename(message.get("filename"), {".tif", ".tiff"})
    size = message.get("size")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or size > MAX_FILE_SIZE
    ):
        raise ValueError("Invalid TIFF size.")

    request_dir = UPLOAD_DIR / request_id
    fax_path = request_dir / f"fax-{request_id}{Path(filename).suffix.lower()}"
    try:
        await _receive_binary(
            websocket,
            {"size": size},
            fax_path,
        )

        async def report_call_sent():
            await _send_json(
                websocket, {"type": "call_sent", "requestId": request_id}
            )

        duration_ms = await fax_controller.recalculate(
            fax_path,
            report_call_sent,
        )
        await _send_json(
            websocket,
            {
                "type": "call_ended",
                "requestId": request_id,
                "durationMS": duration_ms,
            },
        )
    except asyncio.TimeoutError:
        # The GUI owns the user-facing phase timeouts and reports the relevant phase.
        return
    finally:
        shutil.rmtree(request_dir, ignore_errors=True)


async def handler(websocket):
    async for raw_message in websocket:
        if raw_message == "Hello Server!":
            await websocket.send("Hello Client!")
            continue
        if not isinstance(raw_message, str):
            await _send_json(
                websocket,
                {"type": "error", "error": "Expected a JSON command."},
            )
            continue

        request_id = None
        try:
            message = json.loads(raw_message)
            if not isinstance(message, dict):
                raise ValueError("Expected a JSON object.")
            request_id = message.get("requestId")
            command = message.get("type")
            if command == "play_scene":
                await _handle_play_scene(websocket, message)
            elif command == "recalculate_time":
                await _handle_recalculate_time(websocket, message)
            else:
                raise ValueError("Unsupported WebSocket command.")
        except (json.JSONDecodeError, ValueError) as error:
            await _send_json(
                websocket,
                {
                    "type": "error",
                    "requestId": request_id,
                    "error": str(error),
                },
            )
        except Exception:
            LOGGER.exception("WebSocket command failed.")
            await _send_json(
                websocket,
                {
                    "type": "error",
                    "requestId": request_id,
                    "error": "The WebSocket command could not be completed.",
                },
            )


async def main():
    try:
        async with serve(handler, "", 8765, max_size=MAX_FILE_SIZE):
            await asyncio.Future()
    finally:
        await scene_runner.close()


if __name__ == "__main__":
    asyncio.run(main())
