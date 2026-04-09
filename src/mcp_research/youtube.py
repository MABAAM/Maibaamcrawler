"""YouTube Essence Extractor — extract transcripts, summaries, key points, chapters, and quotes."""

import json
import logging
import os
import re
import shutil
import subprocess
import time

from . import config, ollama

logger = logging.getLogger(__name__)

_YT_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})')


def _check_ytdlp() -> tuple[bool, str]:
    if shutil.which("yt-dlp"):
        return True, ""
    return False, "yt-dlp not found. Install: pip install yt-dlp"


def _parse_vtt_transcript(vtt_path: str) -> str:
    """Parse a WebVTT file into plain text, deduplicating repeated auto-sub lines."""
    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.split("\n")
        text_lines: list[str] = []
        seen: set[str] = set()
        for line in lines:
            line = line.strip()
            if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line:
                continue
            if re.match(r"^\d+$", line):
                continue
            clean = re.sub(r"<[^>]+>", "", line)
            if clean and clean not in seen:
                seen.add(clean)
                text_lines.append(clean)
        return " ".join(text_lines)
    except Exception:
        return ""


def _format_timestamped(segments: list[dict]) -> str:
    """Format whisper segments with timestamps for quote extraction."""
    return "\n".join(
        f'[{int(s["start"]) // 60:02d}:{int(s["start"]) % 60:02d}] {s["text"]}'
        for s in segments
    )


def _extract_json_array(raw: str) -> list:
    """Extract a JSON array from LLM response, handling markdown fences."""
    raw = raw.strip()
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        raw = m.group(1).strip() if m else "[]"
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else []


def youtube_essence(url: str, mode: str = "standard", model: str = "",
                    whisper_model: str = "", whisper_device: str = "",
                    on_progress: callable = None) -> dict:
    """Extract essence from a YouTube video.

    Args:
        url: YouTube URL.
        mode: "quick" (TL;DR), "standard" (+ chapters), or "deep" (+ quotes + timestamps).
        model: Ollama model override.
        whisper_model: Whisper model size override (default from config).
        whisper_device: Whisper device override ("cuda", "cpu", "auto").
        on_progress: Optional callback(dict) for progress reporting.

    Returns dict with: title, duration, summary, key_points, chapters, quotes, etc.
    """
    progress = on_progress or (lambda d: None)

    # Validate URL
    m = _YT_URL_RE.match(url)
    if not m:
        return {"error": "Invalid YouTube URL. Supported: youtube.com/watch?v=, youtu.be/, youtube.com/shorts/"}
    video_id = m.group(1)

    mode_config = {
        "quick":    {"chapters": False, "quotes": False, "max_summary": 300, "whisper": "small"},
        "standard": {"chapters": True,  "quotes": False, "max_summary": 800, "whisper": "medium"},
        "deep":     {"chapters": True,  "quotes": True,  "max_summary": 1500, "whisper": "medium"},
    }.get(mode, {"chapters": True, "quotes": False, "max_summary": 800, "whisper": "medium"})

    w_model = whisper_model or config.WHISPER_MODEL or mode_config["whisper"]
    w_device = whisper_device or config.WHISPER_DEVICE or "auto"
    llm_model = model or config.OLLAMA_MODEL

    # Check cache
    cache_path = config.YOUTUBE_CACHE_DIR / f"{video_id}.json"
    mode_order = {"quick": 0, "standard": 1, "deep": 2}
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if mode_order.get(mode, 1) <= mode_order.get(cached.get("mode", "standard"), 1):
                cached["from_cache"] = True
                return cached
        except Exception:
            pass

    # Check yt-dlp
    ok, err = _check_ytdlp()
    if not ok:
        return {"error": err}

    import tempfile
    tmp_dir = os.path.join(tempfile.gettempdir(), f"mcp_yt_{video_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        # Step 1: metadata
        progress({"stage": "metadata", "progress": 0.1})
        meta_proc = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=30)
        if meta_proc.returncode != 0:
            return {"error": f"yt-dlp metadata failed: {meta_proc.stderr[:200]}"}
        meta = json.loads(meta_proc.stdout)
        title = meta.get("title", "")
        dur_s = meta.get("duration", 0)
        duration = f"{int(dur_s // 60)}:{int(dur_s % 60):02d}"

        # Chapters from metadata
        chapters = []
        if mode_config["chapters"] and meta.get("chapters"):
            for ch in meta["chapters"]:
                t = int(ch.get("start_time", 0))
                chapters.append({"time": f"{t // 60:02d}:{t % 60:02d}", "title": ch.get("title", "")})

        # Step 2: download audio
        progress({"stage": "downloading", "progress": 0.2})
        audio_path = os.path.join(tmp_dir, f"{video_id}.wav")
        subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "wav", "--audio-quality", "0",
             "-o", audio_path, "--no-playlist", url],
            capture_output=True, text=True, timeout=600)
        # Find actual file if wav conversion failed
        if not os.path.isfile(audio_path):
            for fname in os.listdir(tmp_dir):
                if fname.startswith(video_id) and not fname.endswith(".json"):
                    audio_path = os.path.join(tmp_dir, fname)
                    break

        # Step 3: transcription
        progress({"stage": "transcribing", "progress": 0.4})
        transcript = ""
        segments: list[dict] = []
        transcription_source = None

        if os.path.isfile(audio_path):
            try:
                from faster_whisper import WhisperModel
                device = w_device if w_device != "auto" else "cuda"
                try:
                    wm = WhisperModel(w_model, device=device, compute_type="float16")
                except Exception:
                    wm = WhisperModel(w_model, device="cpu", compute_type="int8")
                segs, _info = wm.transcribe(audio_path, beam_size=5)
                for seg in segs:
                    segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
                transcript = " ".join(s["text"] for s in segments)
                transcription_source = "faster-whisper"
                logger.info(f"Transcribed {video_id} via faster-whisper ({len(transcript)} chars)")
            except ImportError:
                logger.info("faster-whisper not installed, falling back to auto-subs")
            except Exception as e:
                logger.warning(f"Whisper transcription failed: {e}")

        # Fallback: auto-generated subtitles
        if not transcript:
            subprocess.run(
                ["yt-dlp", "--write-auto-sub", "--sub-lang", "en", "--skip-download",
                 "--sub-format", "vtt", "-o", os.path.join(tmp_dir, "%(id)s"), url],
                capture_output=True, text=True, timeout=60)
            for fname in sorted(os.listdir(tmp_dir)):
                if fname.endswith(".vtt"):
                    transcript = _parse_vtt_transcript(os.path.join(tmp_dir, fname))
                    if transcript:
                        transcription_source = "auto-subs"
                        break

        # Step 4: Ollama structured extraction
        progress({"stage": "extracting", "progress": 0.6})
        summary = ""
        key_points: list[str] = []
        quotes: list[str] = []

        if transcript:
            t_max = 24000 if mode == "deep" else 12000
            t_for_llm = transcript[:t_max]

            # Summary
            summary = ollama.ollama_query(
                prompt=(f"Summarize this YouTube video transcript in {mode_config['max_summary']} words max. "
                        f"Use markdown with headings and bullet points.\n\n"
                        f"Title: {title}\nDuration: {duration}\n\nTranscript:\n{t_for_llm}"),
                system="You are a precise video summarizer. Extract the core ideas with markdown formatting.",
                model=llm_model,
                max_tokens=mode_config["max_summary"] * 2,
            ) or ""

            progress({"stage": "extracting", "progress": 0.75})

            # Key points
            kp_raw = ollama.ollama_query(
                prompt=(f"Extract 5-10 key takeaways from this video transcript as a JSON array of strings. "
                        f"Output ONLY valid JSON.\n\nTitle: {title}\n\nTranscript:\n{t_for_llm[:8000]}"),
                system='Output a JSON array of strings, nothing else. Example: ["point 1", "point 2"]',
                model=llm_model, max_tokens=800,
            )
            if kp_raw:
                try:
                    key_points = _extract_json_array(kp_raw)
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Quotes (deep mode only, requires timestamped segments)
            if mode_config["quotes"] and segments:
                progress({"stage": "extracting_quotes", "progress": 0.85})
                q_raw = ollama.ollama_query(
                    prompt=(f"Extract 5-8 notable direct quotes from this transcript. "
                            f'Format each as "exact quote text [MM:SS]". Output ONLY a JSON array of strings.\n\n'
                            f"Title: {title}\n\nTranscript:\n"
                            f"{_format_timestamped(segments)[:12000]}"),
                    system="Output a JSON array of strings. Each is a quote with timestamp.",
                    model=llm_model, max_tokens=1000,
                )
                if q_raw:
                    try:
                        quotes = _extract_json_array(q_raw)
                    except (json.JSONDecodeError, AttributeError):
                        pass

        progress({"stage": "done", "progress": 1.0})

        result = {
            "url": url, "video_id": video_id, "title": title,
            "duration": duration, "duration_secs": dur_s,
            "summary": summary, "key_points": key_points,
            "chapters": chapters, "quotes": quotes,
            "transcript_length": len(transcript),
            "transcript_excerpt": transcript[:2000] if transcript else "",
            "transcription_source": transcription_source,
            "mode": mode, "from_cache": False,
        }

        # Cache result
        try:
            cache_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        from .fetch import _log_event
        _log_event(url, 1, "youtube_essence", extra={
            "mode": mode, "video_id": video_id,
            "transcript_len": len(transcript), "summary_len": len(summary),
        })

        return result

    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
