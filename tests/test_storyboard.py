import contextlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import batch_sync
import lyric_sync
import storyboard


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pose test '")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.song = self.root / "input" / "Ma Chao 馬超"
        self.song.mkdir(parents=True)
        (self.song / "poses").mkdir()
        # BMP is supported by FFmpeg; write real, dependency-free test images.
        import struct
        for number, color in ((1, (0, 0, 255)), (2, (255, 0, 0)), (10, (0, 255, 0))):
            pixels = bytes(color) * 2 + b"\0\0"
            bmp = struct.pack("<2sIHHI", b"BM", 70, 0, 0, 54)
            bmp += struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 24, 0, 16, 0, 0, 0, 0) + pixels * 2
            (self.song / "poses" / f"{number}.bmp").write_bytes(bmp)
        self.audio = self.song / "song.wav"
        with wave.open(str(self.audio), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 33600)  # 2.1 seconds
        self.lyrics = self.song / "lyrics.txt"
        self.lyrics.write_text("hello world", encoding="utf-8")
        self.config = self.song / "storyboard.json"
        self.data = {"shots": [{"number": n, "image": f"poses/{n}.bmp"} for n in (10, 2, 1)]}
        self.save()

    def save(self):
        self.config.write_text(json.dumps(self.data), encoding="utf-8-sig")

    def batch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return batch_sync.main(self.root)


class ConfigurationTests(Fixture):
    def test_explicit_numeric_order_and_windows_paths(self):
        self.data["shots"][0]["image"] = "poses\\10.bmp"
        self.data["shots"][1]["duration"] = 8
        self.save()
        board = storyboard.load_storyboard(self.config)
        self.assertEqual([s.number for s in board.shots], [1, 2, 10])
        self.assertEqual([s.duration for s in board.shots], [4, 8, 4])

    def test_invalid_configuration(self):
        cases = [[], {"shots": []}, {"shots": "poses"}, {"shots": [None]},
                 {"typo": True}, {"default_duration": 0}, {"transition": "spin"},
                 {"transition_duration": float("nan")}, {"transition_duration": True}]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                self.config.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    storyboard.load_storyboard(self.config)

    def test_invalid_shots(self):
        for field, value in [("number", 0), ("number", True), ("number", 2), ("duration", -1),
                             ("duration", float("inf")), ("image", "missing.png"),
                             ("image", "../outside.png"), ("image", "song.wav"), ("duraton", 5)]:
            with self.subTest(field=field, value=value):
                data = json.loads(json.dumps(self.data))
                data["shots"][0][field] = value
                self.config.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    storyboard.load_storyboard(self.config)

    def test_frame_matching_short_and_long_soundtracks(self):
        board = storyboard.load_storyboard(self.config)
        for duration in (0.1, 2.101, 180.04, 500):
            frames = storyboard.shot_frames(board, duration)
            self.assertEqual(sum(frames), math.ceil(duration * 30))
            self.assertTrue(all(n > 0 for n in frames))
            self.assertEqual(frames, storyboard.shot_frames(board, duration))
        with self.assertRaises(ValueError):
            storyboard.shot_frames(board, 0.01)

    def test_relative_shot_durations(self):
        self.data["shots"][1]["duration"] = 8
        self.save()
        frames = storyboard.shot_frames(storyboard.load_storyboard(self.config), 10)
        self.assertLessEqual(abs(frames[1] - 2 * frames[0]), 2)


class BatchTests(Fixture):
    def fake_assemble(self, board, audio, output, *args):
        self.assertEqual(audio, self.audio)
        output.write_bytes(b"video")

    def fake_sync(self, command):
        self.assertTrue(Path(command[2]).exists())
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"finished")
        output.with_suffix(".srt").write_text("subtitles")
        return subprocess.CompletedProcess(command, 0)

    def test_storyboard_handoff_cache_and_dependencies(self):
        with patch("batch_sync.find_executable", side_effect=lambda n, _: n), \
             patch("batch_sync.assemble", side_effect=self.fake_assemble) as assemble, \
             patch("batch_sync.subprocess.run", side_effect=self.fake_sync) as sync:
            self.assertEqual(self.batch(), 0)
            command = sync.call_args.args[0]
            self.assertEqual(command[command.index("--audio") + 1], str(self.audio))
            self.assertEqual(command[3], str(self.lyrics))
            self.assertEqual(command[command.index("--title") + 1], "song")
            self.assertFalse(Path(command[2]).exists())  # cleaned only after sync
            self.assertEqual(self.batch(), 0)
            self.assertEqual(assemble.call_count, 1)
            for dependency in (self.song / "poses" / "2.bmp", self.config, self.lyrics, self.audio):
                stat = dependency.stat()
                os.utime(dependency, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
                self.assertEqual(self.batch(), 0)
            title = self.song / "title.txt"
            title.write_text("Battle")
            self.assertEqual(self.batch(), 0)
            title.unlink()
            self.assertEqual(self.batch(), 0)
            self.assertEqual(assemble.call_count, 7)
            next((self.root / "finished").glob("*.srt")).unlink()
            self.assertEqual(self.batch(), 0)
            self.assertEqual(assemble.call_count, 8)

    def test_no_config_never_auto_selects_multiple_images(self):
        self.config.unlink()
        for n in (1, 2):
            shutil.copyfile(self.song / "poses" / f"{n}.bmp", self.song / f"{n}.bmp")
        with patch("batch_sync.subprocess.run") as sync:
            self.assertEqual(self.batch(), 1)
            sync.assert_not_called()

    def test_legacy_media_modes(self):
        self.config.unlink()
        for mode in ("audio", "image", "video", "replacement"):
            with self.subTest(mode=mode):
                cover = self.song / "cover.bmp"
                video = self.song / "movie.mp4"
                if mode == "image":
                    shutil.copyfile(self.song / "poses" / "1.bmp", cover)
                if mode in ("video", "replacement"):
                    video.touch()
                if mode == "video":
                    self.audio.unlink()
                with patch("batch_sync.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as sync:
                    self.assertEqual(self.batch(), 0)
                    cmd = sync.call_args.args[0]
                    expected = self.audio if mode == "audio" else cover if mode == "image" else video
                    self.assertEqual(cmd[2], str(expected))
                    self.assertEqual("--audio" in cmd, mode in ("image", "replacement"))
                cover.unlink(missing_ok=True)
                video.unlink(missing_ok=True)
                self.audio.touch()

    def test_ambiguous_storyboard_inputs(self):
        for name in ("extra.wav", "movie.mp4"):
            extra = self.song / name
            extra.touch()
            with patch("batch_sync.assemble") as assemble:
                self.assertEqual(self.batch(), 1)
                assemble.assert_not_called()
            extra.unlink()

    def test_failed_render_continues_batch_and_retries(self):
        with patch("batch_sync.find_executable", return_value="ffmpeg"), \
             patch("batch_sync.assemble", side_effect=ValueError("bad render")), \
             patch("batch_sync.subprocess.run") as sync:
            self.assertEqual(self.batch(), 1)
            sync.assert_not_called()
        with patch("batch_sync.find_executable", return_value="ffmpeg"), \
             patch("batch_sync.assemble", side_effect=self.fake_assemble), \
             patch("batch_sync.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(self.batch(), 1)
            self.assertEqual(list((self.root / "finished").glob("*.json")), [])

    def test_failure_does_not_stop_the_next_song(self):
        second = self.root / "input" / "Z next song"
        second.mkdir()
        (second / "song.wav").touch()
        (second / "lyrics.txt").write_text("hello")
        with patch("batch_sync.find_executable", return_value="ffmpeg"), \
             patch("batch_sync.assemble", side_effect=subprocess.CalledProcessError(1, ["ffmpeg"])), \
             patch("batch_sync.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as sync:
            self.assertEqual(self.batch(), 1)
            self.assertEqual(sync.call_count, 1)
            self.assertEqual(Path(sync.call_args.args[0][2]).parent, second)

    def test_removing_storyboard_does_not_reuse_storyboard_output(self):
        with patch("batch_sync.find_executable", return_value="ffmpeg"), \
             patch("batch_sync.assemble", side_effect=self.fake_assemble), \
             patch("batch_sync.subprocess.run", side_effect=self.fake_sync) as sync:
            self.assertEqual(self.batch(), 0)
            self.config.unlink()
            self.assertEqual(self.batch(), 0)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(sync.call_args.args[0][2], str(self.audio))


class TimingTests(Fixture):
    def test_lyric_pipeline_uses_original_audio_and_preserves_srt_times(self):
        video = self.song / "assembled.mp4"
        video.touch()
        model = self.root / "model.bin"
        model.touch()
        output = self.root / "result.mp4"
        commands = []

        def fake_run(cmd):
            commands.append(cmd)
            if "-osrt" in cmd:
                Path(cmd[cmd.index("-of") + 1]).with_suffix(".srt").write_text(
                    "1\n00:00:00,500 --> 00:00:01,500\nhello world\n", encoding="utf-8")

        argv = ["lyric_sync.py", str(video), str(self.lyrics), "--audio", str(self.audio),
                "--model", str(model), "-o", str(output)]
        with patch.object(sys, "argv", argv), patch("lyric_sync.run", side_effect=fake_run), \
             patch("lyric_sync.find_executable", side_effect=lambda n, _: n), \
             patch("lyric_sync.contains_video", return_value=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lyric_sync.main(), 0)
        self.assertEqual(commands[0][commands[0].index("-i") + 1], str(self.audio))
        self.assertEqual(lyric_sync.parse_srt(output.with_suffix(".srt")), [(500, 1500, "hello world")])
        self.assertIn("1:a:0", commands[-1])
        self.assertIn(str(self.audio), commands[-1])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
class FFmpegTests(Fixture):
    def probe(self, output):
        return json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)]))

    def pixel(self, output, seconds):
        return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(output),
                                        "-frames:v", "1", "-vf", "crop=100:100,scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])

    def test_real_1080p_cut_fade_order_and_duration(self):
        for transition in ("cut", "fade"):
            with self.subTest(transition=transition):
                self.data.update(transition=transition, transition_duration=0.1)
                self.save()
                output = self.root / f"{transition}.mp4"
                with contextlib.redirect_stdout(io.StringIO()):
                    storyboard.assemble(storyboard.load_storyboard(self.config), self.audio, output, "ffmpeg", "ffprobe")
                info = self.probe(output)
                stream = info["streams"][0]
                self.assertEqual(len(info["streams"]), 1)  # audio stays in lyric pipeline
                self.assertEqual((stream["width"], stream["height"], stream["pix_fmt"], stream["r_frame_rate"]),
                                 (1920, 1080, "yuv420p", "30/1"))
                self.assertLess(abs(float(info["format"]["duration"]) - 2.1), 1 / 30)
                for time, channel in ((0.3, 0), (1.0, 2), (1.7, 1)):
                    pixel = self.pixel(output, time)
                    self.assertGreater(pixel[channel], 200)
                if transition == "fade":
                    self.assertLess(max(self.pixel(output, 0.7)), 15)

    def test_single_shot_non_frame_aligned_audio(self):
        self.data["shots"] = self.data["shots"][:1]
        self.data.update(transition="fade", transition_duration=3)
        self.save()
        with wave.open(str(self.audio), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 16016)
        output = self.root / "single.mp4"
        with contextlib.redirect_stdout(io.StringIO()):
            storyboard.assemble(storyboard.load_storyboard(self.config), self.audio, output, "ffmpeg", "ffprobe")
        duration = float(self.probe(output)["format"]["duration"])
        self.assertGreaterEqual(duration, 1.001)
        self.assertLess(duration - 1.001, 1 / 30)

    def test_twenty_five_shots_have_no_cumulative_drift(self):
        self.data["shots"] = [{"number": n, "image": f"poses/{(1, 2, 10)[n % 3]}.bmp"}
                              for n in range(25, 0, -1)]
        self.save()
        output = self.root / "25-shots.mp4"
        with contextlib.redirect_stdout(io.StringIO()):
            storyboard.assemble(storyboard.load_storyboard(self.config), self.audio, output, "ffmpeg", "ffprobe")
        stream = self.probe(output)["streams"][0]
        self.assertEqual(int(stream["nb_frames"]), 63)
        self.assertEqual(float(stream["duration"]), 2.1)

    def test_invalid_audio_and_impossible_fades(self):
        with self.assertRaises(ValueError):
            storyboard.audio_duration("ffprobe", self.song / "poses" / "1.bmp")
        self.data.update(transition="fade", transition_duration=1)
        self.save()
        with patch("storyboard.run") as run:
            with self.assertRaises(ValueError):
                storyboard.assemble(storyboard.load_storyboard(self.config), self.audio, self.root / "out.mp4", "ffmpeg", "ffprobe")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
