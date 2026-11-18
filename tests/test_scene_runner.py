import tempfile
import unittest
from pathlib import Path

from scene_runner import SceneRunner


class SceneRunnerTests(unittest.TestCase):
    def test_resolves_asset_references_without_mutating_source_scene(self):
        asset_id = "0b4e14e1-4a2d-4c64-b891-6972e40cfb32"
        scene = {
            "tracks": [
                {
                    "kind": "action",
                    "actions": [
                        {
                            "id": "action-1",
                            "type": "audio",
                            "kind": "action",
                            "file": asset_id,
                            "startTime": 0,
                            "durationMS": 1000,
                        }
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_path = Path(temp_dir) / "sound.wav"
            asset_path.touch()
            result = SceneRunner.resolve_scene_assets(
                scene, {asset_id: asset_path}
            )

        self.assertEqual(
            result["tracks"][0]["actions"][0]["file"], str(asset_path)
        )
        self.assertEqual(scene["tracks"][0]["actions"][0]["file"], asset_id)

    def test_rejects_missing_assets(self):
        scene = {
            "tracks": [
                {
                    "kind": "action",
                    "actions": [
                        {
                            "id": "action-1",
                            "type": "audio",
                            "file": "missing",
                            "startTime": 0,
                            "durationMS": 1000,
                        }
                    ],
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "missing asset"):
            SceneRunner.resolve_scene_assets(scene, {})

    def test_rejects_non_fax_call_actions(self):
        scene = {
            "tracks": [
                {
                    "kind": "action",
                    "actions": [
                        {
                            "id": "action-1",
                            "type": "call",
                            "sub_type": "phone",
                            "file": "asset-id",
                            "startTime": 0,
                            "durationMS": 1000,
                        }
                    ],
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "Only fax calls"):
            SceneRunner.resolve_scene_assets(scene, {})

    def test_unknown_fax_duration_reserves_one_minute(self):
        asset_id = "0b4e14e1-4a2d-4c64-b891-6972e40cfb32"
        scene = {
            "durationMS": 0,
            "tracks": [
                {
                    "kind": "action",
                    "actions": [
                        {
                            "id": "fax-1",
                            "type": "call",
                            "sub_type": "fax",
                            "file": asset_id,
                            "startTime": 0,
                            "durationMS": 0,
                        }
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_path = Path(temp_dir) / "fax.tif"
            asset_path.touch()
            result = SceneRunner.resolve_scene_assets(
                scene, {asset_id: asset_path}
            )

        self.assertEqual(result["durationMS"], 60_000)

    def test_rejects_invalid_timing(self):
        scene = {
            "tracks": [
                {
                    "kind": "action",
                    "actions": [
                        {
                            "id": "action-1",
                            "type": "audio",
                            "file": "asset-id",
                            "startTime": True,
                            "durationMS": 1000,
                        }
                    ],
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "timing"):
            SceneRunner.resolve_scene_assets(scene, {"asset-id": Path("unused")})

    def test_converts_rgb_channels_to_gpiozero_values(self):
        self.assertEqual(
            SceneRunner._color({"r": 255, "g": 127.5, "b": 0}),
            (1, 0.5, 0),
        )

    def test_rejects_rgb_channels_out_of_range(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 255"):
            SceneRunner._color({"r": 256, "g": 0, "b": 0})


if __name__ == "__main__":
    unittest.main()
