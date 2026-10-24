#!/usr/bin/env python3
"""Build an opt-in, frame-timed 1080p storyboard without changing audio timing."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lyric_sync import IMAGE_EXTENSIONS, find_executable, run

FPS = 30


@dataclass(frozen=True)
class Shot:
    number: int
    image: Path
    duration: float


@dataclass(frozen=True)
class Storyboard:
    shots: tuple[Shot, ...]
    transition: str
    transition_duration: float


def positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def load_storyboard(config: Path) -> Storyboard:
    data = json.loads(config.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) - {"shots", "default_duration", "transition", "transition_duration"}:
        raise ValueError("storyboard.json must be an object with supported storyboard keys")
    default = positive(data.get("default_duration", 4), "default_duration")
    transition = data.get("transition", "cut")
    if transition not in ("cut", "fade"):
        raise ValueError("transition must be cut or fade")
    fade = positive(data.get("transition_duration", 0.5), "transition_duration")
    entries = data.get("shots")
    if not isinstance(entries, list) or not entries:
        raise ValueError("shots must be a nonempty list")
    shots = []
    numbers = set()
    base = config.resolve().parent
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"number", "image", "duration"}:
            raise ValueError("Each shot needs number, image, and optional duration")
        number = entry.get("number")
        if type(number) is not int or number < 1 or number in numbers:
            raise ValueError("Shot numbers must be unique positive integers")
        name = entry.get("image")
        if not isinstance(name, str) or not name:
            raise ValueError(f"Shot {number} needs an image path")
        # Accept Windows separators even when validating on another platform.
        image = (base / name.replace("\\", "/")).resolve()
        if not image.is_relative_to(base) or image.suffix.casefold() not in IMAGE_EXTENSIONS or not image.is_file():
            raise ValueError(f"Shot {number}: image must exist inside the song folder: {name}")
        shots.append(Shot(number, image, positive(entry.get("duration", default), f"Shot {number} duration")))
        numbers.add(number)
    return Storyboard(tuple(sorted(shots, key=lambda shot: shot.number)), transition, fade)


def audio_duration(ffprobe: str, audio: Path) -> float:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=index:format=duration", "-of", "json", str(audio)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise ValueError(f"No audio stream in {audio}")
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Cannot determine soundtrack duration: {audio}") from error
    return positive(duration, "Soundtrack duration")


def shot_frames(board: Storyboard, duration: float) -> list[int]:
    """Apportion the soundtrack's frames proportionally, keeping every shot."""
    total = math.ceil(positive(duration, "Soundtrack duration") * FPS)
    if total < len(board.shots):
        raise ValueError("Soundtrack is too short to give each shot one frame")
    weight = sum(shot.duration for shot in board.shots)
    if not math.isfinite(weight):
        raise ValueError("Total shot duration is too large")
    # Reserve one frame per shot, then distribute the rest by largest remainder.
    quotas = [(total - len(board.shots)) * (shot.duration / weight) for shot in board.shots]
    frames = [1 + math.floor(quota) for quota in quotas]
    order = sorted(range(len(frames)), key=lambda i: (-(quotas[i] % 1), i))
    for i in order[:total - sum(frames)]:
        frames[i] += 1
    return frames


def assemble(board: Storyboard, audio: Path, output: Path, ffmpeg: str, ffprobe: str) -> None:
    frames = shot_frames(board, audio_duration(ffprobe, audio))
    fade_frames = max(1, round(board.transition_duration * FPS)) if board.transition == "fade" and len(board.shots) > 1 else 0
    if fade_frames and any(count < 2 * fade_frames + 1 for count in frames):
        raise ValueError("Scaled shots are too short for transition_duration; shorten the fade or use cut")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Encode one shot at a time: memory use does not grow with the pose count.
    # Only generated ASCII basenames enter the concat file (Windows-safe).
    with tempfile.TemporaryDirectory(prefix="storyboard-") as temp_name:
        temp = Path(temp_name)
        names = []
        for i, (shot, count) in enumerate(zip(board.shots, frames)):
            name = f"shot-{i:06d}.mp4"
            filters = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,format=yuv420p"
            if fade_frames:
                if i:
                    filters += f",fade=t=in:start_frame=0:nb_frames={fade_frames}"
                if i < len(frames) - 1:
                    filters += f",fade=t=out:start_frame={count - fade_frames}:nb_frames={fade_frames}"
            run([ffmpeg, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(shot.image),
                 "-vf", filters, "-frames:v", str(count), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                 "-threads", "2", "-pix_fmt", "yuv420p", "-video_track_timescale", "15360", str(temp / name)])
            names.append(name)
        manifest = temp / "shots.txt"
        manifest.write_text("".join(f"file '{name}'\n" for name in names), encoding="ascii")
        run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(manifest),
             "-map", "0:v:0", "-c:v", "copy", "-an", "-movflags", "+faststart", str(output)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.config.resolve(), args.audio.resolve()}:
        raise ValueError("Output must not overwrite an input")
    board = load_storyboard(args.config)
    if args.output.resolve() in {shot.image for shot in board.shots}:
        raise ValueError("Output must not overwrite a storyboard image")
    tools = Path(__file__).resolve().parent / "tools"
    assemble(board, args.audio, args.output, find_executable("ffmpeg", tools), find_executable("ffprobe", tools))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"ERROR: {error}")
