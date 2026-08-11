#!/usr/bin/env python3
"""Process every song subfolder inside input/."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


MEDIA_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def choose_lyrics(files: list[Path]) -> Path | None:
    text_files = [p for p in files if p.suffix.casefold() == ".txt"]
    named = [p for p in text_files if "lyric" in p.stem.casefold()]
    return named[0] if named else (text_files[0] if len(text_files) == 1 else None)


def main() -> int:
    root = Path(__file__).resolve().parent
    input_root = root / "input"
    finished = root / "finished"
    finished.mkdir(exist_ok=True)
    song_folders = sorted(p for p in input_root.iterdir() if p.is_dir())
    if not song_folders:
        print(f"No song folders found in {input_root}")
        return 1

    failures = []
    processed = 0
    for folder in song_folders:
        files = [p for p in folder.iterdir() if p.is_file()]
        candidates = [p for p in files if p.suffix.casefold() in MEDIA_EXTENSIONS and "with-lyrics" not in p.stem.casefold()]
        videos = [p for p in candidates if p.suffix.casefold() in VIDEO_EXTENSIONS]
        audio_files = [p for p in candidates if p.suffix.casefold() in AUDIO_EXTENSIONS]
        images = [p for p in files if p.suffix.casefold() in IMAGE_EXTENSIONS]
        if videos:
            media = videos
            replacement_audio = audio_files[0] if len(audio_files) == 1 else None
        elif images:
            media = images
            replacement_audio = audio_files[0] if len(audio_files) == 1 else None
        else:
            media = audio_files
            replacement_audio = None
        lyrics = choose_lyrics(files)
        image_without_audio = bool(images and not videos and len(audio_files) != 1)
        if len(media) != 1 or lyrics is None or image_without_audio:
            print(f"SKIP {folder.name}: needs lyrics plus one video, one audio file, or one image paired with one audio file")
            failures.append(folder.name)
            continue
        output = finished / f"{folder.name}-with-lyrics.mp4"
        style_file = folder / "style.txt"
        dependencies = [media[0], lyrics] + ([replacement_audio] if replacement_audio else []) + ([style_file] if style_file.exists() else [])
        if output.exists() and output.stat().st_mtime >= max(p.stat().st_mtime for p in dependencies):
            print(f"SKIP {folder.name}: output is already up to date")
            continue
        print(f"\nPROCESSING {folder.name}")
        title_file = folder / "title.txt"
        title = title_file.read_text(encoding="utf-8-sig").strip() if title_file.exists() else ""
        if not title:
            title = media[0].stem
        command = [sys.executable, str(root / "lyric_sync.py"), str(media[0]), str(lyrics), "-o", str(output), "--title", title]
        if replacement_audio:
            command += ["--audio", str(replacement_audio)]
        if style_file.exists():
            equalizer_style = style_file.read_text(encoding="utf-8-sig").strip().casefold()
            if equalizer_style:
                command += ["--equalizer-style", equalizer_style]
        result = subprocess.run(command)
        if result.returncode:
            failures.append(folder.name)
        else:
            processed += 1

    print(f"\nFinished: {processed} song(s) processed; {len(failures)} failed or skipped due to missing files.")
    if failures:
        print("Check:", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
