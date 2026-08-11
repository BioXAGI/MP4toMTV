#!/usr/bin/env python3
"""Align official, untimed lyrics to a music video using whisper.cpp."""

from __future__ import annotations

import argparse
import colorsys
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from difflib import SequenceMatcher
from collections import Counter
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TIME_RE = re.compile(r"(\d\d):(\d\d):(\d\d)[,.](\d\d\d)")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
EQUALIZER_STYLES = ("dots", "neon-blocks", "neon-particles", "mirror-bars")
FALLBACK_PALETTE = ((35, 205, 255), (190, 55, 255))


def run(command: list[str]) -> None:
    print("+", " ".join(f'"{x}"' if " " in x else x for x in command), flush=True)
    subprocess.run(command, check=True)


def ms(value: str) -> int:
    h, m, s, n = map(int, TIME_RE.fullmatch(value.strip()).groups())
    return ((h * 60 + m) * 60 + s) * 1000 + n


def stamp(value: int) -> str:
    value = max(0, round(value))
    h, value = divmod(value, 3_600_000)
    m, value = divmod(value, 60_000)
    s, value = divmod(value, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{value:03d}"


def clean_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Keep Latin words as tokens and CJK ideographs as individual tokens.
    return re.findall(r"[a-z0-9']+|[\u3400-\u9fff]", text)


def read_lyrics(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or re.fullmatch(r"\[(intro|verse.*|chorus.*|bridge.*|outro|instrumental.*|solo.*)\]", line, re.I):
            continue
        lines.append(line)
    if not lines:
        raise ValueError("The lyrics file contains no lyric lines.")
    return lines


def parse_srt(path: Path) -> list[tuple[int, int, str]]:
    cues = []
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    for block in blocks:
        rows = block.splitlines()
        timing_index = next((i for i, row in enumerate(rows) if "-->" in row), None)
        if timing_index is None:
            continue
        left, right = rows[timing_index].split("-->", 1)
        match_left, match_right = TIME_RE.search(left), TIME_RE.search(right)
        if match_left and match_right:
            text = " ".join(rows[timing_index + 1 :]).strip()
            if text:
                cues.append((ms(match_left.group()), ms(match_right.group()), text))
    return cues


def timed_words(cues: list[tuple[int, int, str]]) -> list[tuple[str, int, int]]:
    result = []
    for start, end, text in cues:
        words = clean_words(text)
        if not words:
            continue
        span = max(1, end - start)
        for i, word in enumerate(words):
            result.append((word, start + span * i // len(words), start + span * (i + 1) // len(words)))
    return result


def align(lines: list[str], transcript: list[tuple[str, int, int]]) -> list[tuple[int, int, str, float]]:
    spoken = [word for word, _, _ in transcript]
    cursor = 0
    output = []
    for line in lines:
        target = clean_words(line)
        expected = max(1, len(target))
        search_end = min(len(spoken), cursor + max(80, expected * 8))
        best = None
        min_len, max_len = max(1, expected - 3), expected + 5
        for start in range(max(0, cursor - 2), search_end):
            for size in range(min_len, max_len + 1):
                end = start + size
                if end > len(spoken):
                    break
                score = SequenceMatcher(None, target, spoken[start:end], autojunk=False).ratio()
                distance_penalty = min(0.12, max(0, start - cursor) * 0.0015)
                candidate = (score - distance_penalty, score, start, end)
                if best is None or candidate > best:
                    best = candidate
        if best is None:
            raise RuntimeError("Not enough recognized vocals to align all lyric lines.")
        _, score, start, end = best
        if score < 0.28:
            print(f"Warning: omitting lyric line not detected in vocals: {line}")
            continue
        start_ms, end_ms = transcript[start][1], transcript[end - 1][2]
        if output:
            start_ms = max(start_ms, output[-1][1] + 20)
        end_ms = max(start_ms + 700, end_ms)
        output.append((start_ms, end_ms, line, score))
        cursor = max(cursor + 1, end)
    if not output:
        raise RuntimeError("None of the supplied lyric lines could be matched to the vocals.")
    return output


def write_srt(path: Path, aligned: list[tuple[int, int, str, float]]) -> None:
    chunks = []
    for index, (start, end, line, _) in enumerate(aligned, 1):
        chunks.append(f"{index}\n{stamp(start)} --> {stamp(end)}\n{line}\n")
    path.write_text("\n".join(chunks), encoding="utf-8-sig")


def find_executable(name: str, tools: Path) -> str:
    local = next(iter(tools.rglob(name + ".exe")), None) if tools.exists() else None
    found = str(local) if local else shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} was not found. Run setup.ps1 first.")
    return found


def contains_video(ffprobe: str, media: Path) -> bool:
    result = subprocess.run(
        # Uppercase V excludes attached cover images found in many audio files.
        [ffprobe, "-v", "error", "-select_streams", "V:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(media)],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def contains_cover_art(ffprobe: str, media: Path) -> bool:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=index", "-of", "csv=p=0", str(media)],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def cover_palette(ffmpeg: str, image: Path) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Sample two vivid, distinct cover colors; fall back for dark/neutral art."""
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-i", str(image), "-vf", "scale=64:64", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=True,
            capture_output=True,
        )
        pixels = [tuple(result.stdout[i : i + 3]) for i in range(0, len(result.stdout) - 2, 3)]
        bins = Counter((r // 16 * 16, g // 16 * 16, b // 16 * 16) for r, g, b in pixels)
        candidates = []
        for color, count in bins.items():
            r, g, b = color
            h, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if value < 0.20 or saturation < 0.18:
                continue
            candidates.append((count * (0.35 + saturation) * (0.4 + value), color, count, h, saturation, value))
        if not candidates:
            return FALLBACK_PALETTE
        candidates.sort(reverse=True, key=lambda item: item[0])
        first = candidates[0][1]
        first_hue = candidates[0][3]
        second = max(
            candidates[1:] or candidates,
            key=lambda item: (
                item[2] ** 0.5
                * (0.2 + item[4])
                * (0.2 + item[5])
                * (0.2 + 4 * min(abs(first_hue - item[3]), 1 - abs(first_hue - item[3])))
            ),
        )[1]
        if sum((a - b) ** 2 for a, b in zip(first, second)) < 1_600:
            return FALLBACK_PALETTE

        def vivid(color: tuple[int, int, int]) -> tuple[int, int, int]:
            h, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in color))
            return tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(h, max(0.68, saturation), max(0.82, value)))

        return vivid(first), vivid(second)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return FALLBACK_PALETTE


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-align official lyrics and burn them onto a music video or still image.")
    parser.add_argument("video", type=Path, help="Input video, audio file, or still image")
    parser.add_argument("lyrics", type=Path, help="UTF-8 text file; one subtitle line per line")
    parser.add_argument("-o", "--output", type=Path, help="Output MP4 path")
    parser.add_argument("--language", default="auto", help="Language code such as en, es, zh, or auto")
    parser.add_argument("--model", type=Path, help="Whisper.cpp model path")
    parser.add_argument("--font-size", type=int, default=28)
    parser.add_argument("--title", help="Song title shown during the first three seconds")
    parser.add_argument("--audio", type=Path, help="Optional higher-quality replacement audio")
    parser.add_argument("--equalizer-style", choices=EQUALIZER_STYLES, default="dots", help="Still-image equalizer design")
    parser.add_argument("--subtitles-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    tools = root / "tools"
    ffmpeg = find_executable("ffmpeg", tools)
    ffprobe = find_executable("ffprobe", tools)
    whisper = find_executable("whisper-cli", tools)
    model = args.model or tools / "models" / "ggml-small.bin"
    if not model.exists():
        raise FileNotFoundError(f"Whisper model not found: {model}. Run setup.ps1 first.")
    if not args.video.exists() or not args.lyrics.exists() or (args.audio and not args.audio.exists()):
        raise FileNotFoundError("The video, lyrics, or replacement audio file does not exist.")
    is_still_image = args.video.suffix.casefold() in IMAGE_EXTENSIONS
    if is_still_image and not args.audio:
        raise ValueError("A still image input requires an audio file supplied with --audio.")

    lines = read_lyrics(args.lyrics)
    output = args.output or args.video.with_name(args.video.stem + "-with-lyrics.mp4")
    subtitle = output.with_suffix(".srt")
    with tempfile.TemporaryDirectory(prefix="lyric-sync-") as temp_name:
        temp = Path(temp_name)
        wav = temp / "audio.wav"
        transcript_base = temp / "transcript"
        audio_source = args.audio or args.video
        run([ffmpeg, "-y", "-i", str(audio_source), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        prompt = " ".join(lines)
        command = [whisper, "-m", str(model), "-f", str(wav), "-osrt", "-of", str(transcript_base), "--prompt", prompt, "-l", args.language]
        run(command)
        cues = parse_srt(transcript_base.with_suffix(".srt"))
        words = timed_words(cues)
        if not words:
            raise RuntimeError("Whisper did not detect any vocals.")
        aligned = align(lines, words)
        write_srt(subtitle, aligned)

    weak = [(i + 1, score) for i, (*_, score) in enumerate(aligned) if score < 0.45]
    print(f"Created timed subtitles: {subtitle}")
    if weak:
        print("Warning: review these low-confidence lyric lines:", ", ".join(str(i) for i, _ in weak))
    if not args.subtitles_only:
        escaped = subtitle.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
        style = f"FontName=Arial,FontSize={args.font_size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=45"
        subtitle_filter = f"subtitles='{escaped}':force_style='{style}'"
        title = args.title or args.video.stem
        escaped_title = title.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")
        title_filter = (
            r"drawtext=fontfile='C\:/Windows/Fonts/msyh.ttc':"
            f"text='{escaped_title}':fontcolor=white:fontsize=64:borderw=4:bordercolor=black:"
            "x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,0,3)'"
        )
        if is_still_image:
            # Keep lyric alignment/SRT generation independent from rendering. The
            # audio is split only here so it can drive both the soundtrack and a
            # transparent frequency-bar overlay above the subtitles.
            palette_start, palette_end = cover_palette(ffmpeg, args.video)
            start_hex = "0x" + "".join(f"{channel:02x}" for channel in palette_start)
            end_hex = "0x" + "".join(f"{channel:02x}" for channel in palette_end)
            r1, g1, b1 = palette_start
            r2, g2, b2 = palette_end
            particle_color = tuple(min(255, round((a + b) / 2 * 1.12)) for a, b in zip(palette_start, palette_end))
            print(f"Equalizer palette: {start_hex} -> {end_hex}")
            if args.equalizer_style in {"neon-blocks", "neon-particles"}:
                visualizer = (
                    "[eqin]showfreqs=s=60x18:r=30:mode=bar:ascale=log:fscale=log:averaging=2:"
                    "colors=white,scale=1440:252:flags=neighbor,format=rgba,"
                    "colorkey=black:0.04:0.08,"
                    f"geq=r='{r1}+({r2-r1})*X/W':g='{g1}+({g2-g1})*X/W':b='{b1}+({b2-b1})*X/W':"
                    "a='if(lt(mod(X\\,24)\\,19)*lt(mod(Y\\,14)\\,9)\\,alpha(X\\,Y)*0.68\\,0)'[visual];"
                )
            elif args.equalizer_style == "mirror-bars":
                visualizer = (
                    "[eqin]showfreqs=s=96x42:r=30:mode=bar:ascale=sqrt:fscale=log:averaging=3:"
                    "colors=white,scale=1440:252:flags=neighbor,format=rgba,"
                    "colorkey=black:0.04:0.08,"
                    f"geq=r='{r1}+({r2-r1})*X/W':g='{g1}+({g2-g1})*X/W':b='{b1}+({b2-b1})*X/W':"
                    "a='if(lt(mod(X\\,15)\\,8)\\,alpha(X\\,Y)*0.62\\,0)'[visual];"
                )
            else:
                visualizer = (
                    "[eqin]showfreqs=s=150x36:r=30:mode=bar:ascale=log:fscale=log:averaging=2:"
                    f"colors={start_hex}|{end_hex},scale=1500:252:flags=neighbor,format=rgba,"
                    "colorkey=black:0.05:0.08,"
                    "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                    "a='if(lte(hypot(mod(X\\,10)-5\\,mod(Y\\,7)-3.5)\\,2.65)\\,alpha(X\\,Y)\\,0)'[visual];"
                )
            if args.equalizer_style == "neon-particles":
                particle_effect = (
                    "nullsrc=s=375x32:r=30,format=rgba,"
                    f"geq=r='{particle_color[0]}':g='{particle_color[1]}':b='{particle_color[2]}':"
                    "a='if(lt(abs(mod(sin(X*12.9898+floor(Y+N/8)*78.233)*43758.5453\\,1))\\,0.0035)"
                    "*lt(abs(Y-18)\\,11)\\,210\\,0)',"
                    "scale=1500:128:flags=neighbor[particles];"
                    "[particles]split=2[particlesharp][particleglowsrc];"
                    "[particleglowsrc]gblur=sigma=4,colorchannelmixer=aa=0.55[particleglow];"
                    "[withreflection][particleglow]overlay=(W-w)/2:H-398[particlelit];"
                    "[particlelit][particlesharp]overlay=(W-w)/2:H-398[withfx];"
                )
                final_layer = "[withfx]"
            else:
                particle_effect = ""
                final_layer = "[withreflection]"
            image_filter = (
                "[0:v:0]scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#101522,fps=30[bg];"
                "[1:a:0]asplit=2[aout][eqin];"
                + visualizer
                + "[visual]split=3[sharp][glowsrc][reflectsrc];"
                "[glowsrc]gblur=sigma=5,colorchannelmixer=aa=0.42[glow];"
                "[reflectsrc]vflip,crop=iw:70:0:0,gblur=sigma=2,colorchannelmixer=aa=0.16[reflection];"
                "[bg][glow]overlay=(W-w)/2:H-h-300[lit];"
                "[lit][sharp]overlay=(W-w)/2:H-h-300[withvisual];"
                "[withvisual][reflection]overlay=(W-w)/2:H-300[withreflection];"
                + particle_effect
                + final_layer
                + f"{title_filter},{subtitle_filter}[v]"
            )
            render = [ffmpeg, "-y", "-loop", "1", "-i", str(args.video), "-i", str(args.audio), "-filter_complex", image_filter, "-map", "[v]", "-map", "[aout]", "-shortest", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", str(output)]
        elif contains_video(ffprobe, args.video):
            if args.audio:
                render = [ffmpeg, "-y", "-i", str(args.video), "-i", str(args.audio), "-vf", f"{title_filter},{subtitle_filter}", "-map", "0:v:0", "-map", "1:a:0", "-shortest", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "aac", "-b:a", "320k", str(output)]
            else:
                render = [ffmpeg, "-y", "-i", str(args.video), "-vf", f"{title_filter},{subtitle_filter}", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy", str(output)]
        elif contains_cover_art(ffprobe, args.video):
            cover_filter = (
                "[0:v:0]scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=#101522,"
                f"loop=loop=-1:size=1:start=0,fps=30,{title_filter},{subtitle_filter}[v]"
            )
            render = [ffmpeg, "-y", "-i", str(args.video), "-filter_complex", cover_filter, "-map", "[v]", "-map", "0:a:0", "-shortest", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(output)]
        else:
            render = [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=#101522:s=1920x1080:r=30", "-i", str(args.video), "-vf", f"{title_filter},{subtitle_filter}", "-shortest", "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(output)]
        run(render)
        print(f"Created lyric video: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
