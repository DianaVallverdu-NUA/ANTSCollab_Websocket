"""Execute GUI scenes on the Raspberry Pi."""

import asyncio
import json
import logging
import math
import os
import shutil
import subprocess
import time
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class FaxController:
    def __init__(self):
        self.endpoint_id = os.environ.get("ASTERISK_FAX_ENDPOINT_ID", "1000")
        self.channel = os.environ.get(
            "ASTERISK_FAX_CHANNEL", f"PJSIP/{self.endpoint_id}"
        )
        self._call_lock = asyncio.Lock()

    async def recalculate(self, fax_path, on_call_sent):
        async with self._call_lock:
            if await self._endpoint_busy():
                raise RuntimeError("The fax endpoint is already in use.")

            command = (
                f"channel originate {self.channel} application "
                f"SendFax {fax_path}"
            )
            process = await self._start_asterisk_command(command)
            command_result = asyncio.create_task(process.communicate())
            try:
                await self._wait_for_endpoint_state(True, process, command_result, 30)
                started_at = time.monotonic()
                await on_call_sent()
                await self._wait_for_endpoint_state(False, process, command_result, 300)
                return max(1, round((time.monotonic() - started_at) * 1000))
            finally:
                await self._finish_command(process, command_result)

    async def send_fax(self, fax_path):
        async with self._call_lock:
            if await self._endpoint_busy():
                raise RuntimeError("The fax endpoint is already in use.")
            command = (
                f"channel originate {self.channel} application "
                f"SendFax {fax_path}"
            )
            process = await self._start_asterisk_command(command)
            command_result = asyncio.create_task(process.communicate())
            try:
                await self._wait_for_endpoint_state(
                    True, process, command_result, 30
                )
                await self._wait_for_endpoint_state(
                    False, process, command_result, 300
                )
            finally:
                await self._finish_command(process, command_result)

    async def _start_asterisk_command(self, command):
        try:
            return await asyncio.create_subprocess_exec(
                "asterisk",
                "-rx",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as error:
            raise RuntimeError("The Asterisk CLI is not installed or available.") from error

    async def _endpoint_busy(self):
        process = await self._start_asterisk_command("pjsip list endpoints")
        output, _ = await process.communicate()
        if process.returncode:
            raise RuntimeError(
                f"Could not read Asterisk endpoint status: "
                f"{output.decode(errors='replace').strip()}"
            )

        marker = f"{self.endpoint_id}/{self.endpoint_id}"
        for line in output.decode(errors="replace").splitlines():
            if marker not in line:
                continue
            lowered = line.lower()
            if "not in use" in lowered or "unavailable" in lowered:
                return False
            return "in use" in lowered or "ringing" in lowered or "busy" in lowered
        raise RuntimeError(
            f"Asterisk endpoint {self.endpoint_id} was not found."
        )

    async def _wait_for_endpoint_state(
        self, busy, command_process, command_result, timeout_seconds
    ):
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if command_result.done():
                output, _ = command_result.result()
                self._raise_for_command_error(command_process, output)

            if await self._endpoint_busy() is busy:
                return
            await asyncio.sleep(0.5)
        raise asyncio.TimeoutError

    @staticmethod
    def _raise_for_command_error(process, output):
        response = output.decode(errors="replace").strip()
        lowered = response.lower()
        if process.returncode or any(
            marker in lowered for marker in ("no such command", "no such application", "unable to")
        ):
            raise RuntimeError(f"Asterisk could not complete the fax call: {response}")

    @staticmethod
    async     def _finish_command(process, command_result):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        if not command_result.done():
            try:
                await asyncio.wait_for(asyncio.shield(command_result), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
                await command_result


class SceneRunner:
    def __init__(self, fax_controller):
        self.fax_controller = fax_controller
        self._scene_task = None
        self._led_task = None
        self._led = None

    @staticmethod
    def resolve_scene_assets(scene, assets):
        runnable_scene = json.loads(json.dumps(scene))
        tracks = runnable_scene.get("tracks")
        if not isinstance(tracks, list):
            raise ValueError("Scene tracks must be a list.")

        scene_duration = runnable_scene.get("durationMS", 0)
        if (
            isinstance(scene_duration, bool)
            or not isinstance(scene_duration, (int, float))
            or not math.isfinite(scene_duration)
            or scene_duration < 0
        ):
            raise ValueError("Scene duration must be non-negative.")
        computed_duration = scene_duration

        for track in tracks:
            if not isinstance(track, dict) or not isinstance(
                track.get("actions"), list
            ):
                raise ValueError("Each scene track must contain an action list.")
            for action in track["actions"]:
                if not isinstance(action, dict):
                    raise ValueError("Invalid scene action.")
                action_type = action.get("type")
                if action_type not in {
                    "audio",
                    "call",
                    "led_strip",
                    "led_strip_sequence",
                    "blank",
                }:
                    raise ValueError(f"Unsupported scene action: {action_type}.")
                if action_type == "call" and action.get("sub_type") != "fax":
                    raise ValueError("Only fax calls are supported in scene playback.")
                start_time = action.get("startTime")
                duration = action.get("durationMS")
                if (
                    isinstance(start_time, bool)
                    or not isinstance(start_time, (int, float))
                    or not math.isfinite(start_time)
                    or start_time < 0
                    or isinstance(duration, bool)
                    or not isinstance(duration, (int, float))
                    or not math.isfinite(duration)
                    or duration < 0
                ):
                    raise ValueError("Scene action timing must be non-negative.")
                action_duration = duration
                if (
                    action_type == "call"
                    and duration < 1000
                ):
                    action_duration = 60_000
                computed_duration = max(
                    computed_duration,
                    start_time + action_duration,
                )
                if action_type == "blank":
                    continue
                asset_id = action.get("file")
                if not isinstance(asset_id, str) or asset_id not in assets:
                    raise ValueError("A scene action references a missing asset.")
                asset_path = Path(assets[asset_id])
                if not asset_path.is_file():
                    raise ValueError("A scene action references an unavailable asset.")
                action["file"] = str(asset_path)
                if action_type == "led_strip_sequence":
                    frames = action.get("frames")
                    if not isinstance(frames, list) or not frames:
                        raise ValueError("Light sequences must contain frames.")
                    sequence_duration = 0
                    for frame in frames:
                        if not isinstance(frame, dict):
                            raise ValueError("Light sequence frames must be objects.")
                        SceneRunner._color(frame.get("colour"))
                        frame_duration = frame.get("duration")
                        if (
                            isinstance(frame_duration, bool)
                            or not isinstance(frame_duration, (int, float))
                            or not math.isfinite(frame_duration)
                            or frame_duration <= 0
                        ):
                            raise ValueError(
                                "Light frame durations must be positive."
                            )
                        sequence_duration += frame_duration
                    computed_duration = max(
                        computed_duration,
                        start_time + sequence_duration,
                    )
        runnable_scene["durationMS"] = computed_duration
        return runnable_scene

    async def play(self, scene, asset_dir):
        await self.stop()
        self._scene_task = asyncio.create_task(self._run_scene(scene, asset_dir))

    async def stop(self):
        task = self._scene_task
        self._scene_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run_scene(self, scene, asset_dir):
        action_tasks = set()
        fax_tasks = set()
        try:
            actions = [
                action
                for track in scene["tracks"]
                for action in track["actions"]
            ]
            actions.sort(key=lambda action: action["startTime"])
            loop = asyncio.get_running_loop()
            start = loop.time()
            for action in actions:
                wait_seconds = start + action["startTime"] / 1000 - loop.time()
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                task = asyncio.create_task(self._run_action(action))
                action_tasks.add(task)
                task.add_done_callback(action_tasks.discard)
                if action["type"] == "call":
                    fax_tasks.add(task)
                    task.add_done_callback(fax_tasks.discard)

            scene_duration = scene.get("durationMS", 0)
            remaining = start + scene_duration / 1000 - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)
            if fax_tasks:
                await asyncio.gather(*tuple(fax_tasks))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Scene playback failed.")
        finally:
            for task in tuple(action_tasks):
                task.cancel()
            if action_tasks:
                await asyncio.gather(*action_tasks, return_exceptions=True)
            await self._turn_led_off()
            shutil.rmtree(asset_dir, ignore_errors=True)

    async def _run_action(self, action):
        try:
            action_type = action["type"]
            if action_type == "call":
                await self.fax_controller.send_fax(Path(action["file"]))
            elif action_type == "audio":
                await self._play_audio(action)
            elif action_type in ("led_strip", "led_strip_sequence"):
                await self._play_light(action)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Could not run scene action %s.", action.get("id"))

    async def _play_audio(self, action):
        executable = shutil.which("cvlc") or shutil.which("vlc")
        if not executable:
            raise RuntimeError("VLC is not installed or available.")

        command = [executable, "--intf", "dummy", "--no-video", "--play-and-exit"]
        if action.get("repeat") or action.get("loops"):
            command.append("--loop")
        command.append(action["file"])
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        finished_naturally = False
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=max(0.1, action["durationMS"] / 1000),
            )
            finished_naturally = True
        except asyncio.TimeoutError:
            pass
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        if finished_naturally and process.returncode:
            raise RuntimeError("VLC could not play the scene audio asset.")

    async def _play_light(self, action):
        current_task = asyncio.current_task()
        previous_task = self._led_task
        if previous_task and previous_task is not current_task and not previous_task.done():
            previous_task.cancel()
            await asyncio.gather(previous_task, return_exceptions=True)
        self._led_task = current_task

        try:
            led = self._get_led()
            if action["type"] == "led_strip_sequence":
                for frame in action["frames"]:
                    led.color = self._color(frame["colour"])
                    await asyncio.sleep(max(0, frame["duration"]) / 1000)
            else:
                await self._pulse_led(led, action)
        finally:
            if self._led_task is current_task:
                self._led_task = None
                await self._turn_led_off()

    async def _pulse_led(self, led, action):
        asset = json.loads(Path(action["file"]).read_text(encoding="utf-8"))
        if not isinstance(asset, dict) or asset.get("type") != "pulsing":
            raise ValueError("State light asset must contain pulsing light data.")
        period = asset.get("durationMS")
        if isinstance(period, bool) or not isinstance(period, (int, float)) or period <= 0:
            raise ValueError("Pulsing light duration must be positive.")
        led.pulse(
            fade_in_time=period / 2000,
            fade_out_time=period / 2000,
            on_color=self._color(asset.get("startColour")),
            off_color=self._color(asset.get("endColour")),
            n=None,
            background=True,
        )
        await asyncio.sleep(max(0, action["durationMS"]) / 1000)

    def _get_led(self):
        if self._led is None:
            try:
                from gpiozero import RGBLED
            except ImportError as error:
                raise RuntimeError("gpiozero is required for light playback.") from error
            self._led = RGBLED(
                red=int(os.environ.get("GPIO_RED_PIN", "17")),
                green=int(os.environ.get("GPIO_GREEN_PIN", "27")),
                blue=int(os.environ.get("GPIO_BLUE_PIN", "22")),
            )
        return self._led

    @staticmethod
    def _color(value):
        if not isinstance(value, dict):
            raise ValueError("Light colours must be RGB objects.")
        channels = []
        for key in ("r", "g", "b"):
            channel = value.get(key)
            if (
                isinstance(channel, bool)
                or not isinstance(channel, (int, float))
                or not 0 <= channel <= 255
            ):
                raise ValueError("RGB channels must be between 0 and 255.")
            channels.append(channel / 255)
        return tuple(channels)

    async def _turn_led_off(self):
        if self._led is None:
            return
        self._led.off()

    async def close(self):
        await self.stop()
        if self._led is not None:
            self._led.close()
            self._led = None
