# ANTS Collaboration WebSocket server

The server accepts scene playback and fax-time recalculation requests from the
Electron GUI. Run it on the Raspberry Pi with the WebSocket server listening on
port `8765`:

```sh
python -m pip install -e .
python serve.py
```

The default upload directory is `/fax/websocket_assets` when `/fax` exists, or
`fax_tests` beside `serve.py` during development. Set
`ANTSCOLLAB_WEBSOCKET_DATA` to override it.

## Play Scene

The GUI sends one JSON text frame followed by one binary frame per asset:

```json
{
  "type": "play_scene",
  "requestId": "UUID",
  "scene": {
    "id": "scene-id",
    "name": "Scene",
    "durationMS": 0,
    "tracks": []
  },
  "assets": [
    { "id": "UUID", "filename": "UUID.wav", "size": 1234 }
  ]
}
```

The scene's action `file` fields reference asset IDs. Once all assets arrive,
the server starts the scene and sends an acceptance message. The GUI does not
wait for that response. Time-based audio, fax, and light actions are executed
by the Raspberry Pi; blank actions only reserve timeline duration.

## Recalculate fax time

The GUI sends TIFF metadata and then the TIFF bytes:

```json
{
  "type": "recalculate_time",
  "requestId": "UUID",
  "filename": "fax.tif",
  "size": 1234
}
```

The server originates a `SendFax` call through Asterisk's configured fax
endpoint. It sends `call_sent` when the endpoint becomes busy, followed by
`call_ended` with `durationMS` when it returns to idle. The GUI applies its
30-second call-start and 5-minute call-end timeout behavior.

## Raspberry Pi requirements

- Disable power saving by following [these instructions](https://thedigitalpictureframe.com/stay-connected-enhancing-raspberry-pi-wi-fi-stability-by-turning-off-power-management/)
- The Asterisk CLI must be available, with `SendFax` installed and a fax
  endpoint named `1000`. Override the endpoint ID with
  `ASTERISK_FAX_ENDPOINT_ID`; override the channel with `ASTERISK_FAX_CHANNEL`.
- VLC (`cvlc` or `vlc`) is required for audio actions.
- `gpiozero` is required for light actions. The default RGB LED pins are 17,
  27, and 22; override them with `GPIO_RED_PIN`, `GPIO_GREEN_PIN`, and
  `GPIO_BLUE_PIN`.
- Scene uploads are limited to 100 MiB total and 256 assets per scene; fax TIFF
  uploads are limited to 100 MiB.
- Stop the legacy step-based `/fax/python/manager.py` while using this runner;
  both processes control the same Asterisk, audio, and light hardware.
