"""Deep Ingest — crawl directories and extract information from all file types."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import _extractors, config, ollama

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    "node_modules", ".venv", "venv", "env", ".git", "dist", "build",
    "__pycache__", ".claude", "coverage", ".nyc_output", ".tox", ".mypy_cache",
    ".idea", ".vscode", ".vs", "bin", "obj",
}

_MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB


def deep_ingest(path: str, include_types: str = "",
                max_files: int = 200, max_file_size_mb: int = 50,
                summarize: bool = False, model: str = "",
                whisper_model: str = "", whisper_device: str = "",
                on_progress: callable = None) -> dict:
    """Crawl a directory (or single file) and extract text from all supported file types.

    Args:
        path: Directory or file path to ingest.
        include_types: Comma-separated type filter (text,pdf,audio,video,image,office). Empty = all.
        max_files: Maximum files to process.
        max_file_size_mb: Maximum single file size in MB.
        summarize: If true, generate an Ollama summary of the combined content.
        model: Ollama model override.
        whisper_model: Whisper model size override.
        whisper_device: Whisper device override.
        on_progress: Optional callback(dict) for progress reporting.

    Returns dict with: files_processed, files_skipped, by_type, content, errors, summary.
    """
    progress = on_progress or (lambda d: None)
    path = os.path.normpath(path)
    max_size = max_file_size_mb * 1024 * 1024

    # Single-file fallback
    if os.path.isfile(path):
        progress({"stage": "extracting", "progress": 0.5})
        text, err = _extractors.extract(path, whisper_model, whisper_device)
        ext = os.path.splitext(path)[1].lower()
        ftype = _extractors.get_file_type(ext) or "unknown"
        if err:
            return {
                "files_processed": 0, "files_skipped": 1,
                "by_type": {ftype: {"ok": 0, "error": 1}},
                "content": [], "errors": [f"{os.path.basename(path)}: {err}"], "summary": None,
            }
        progress({"stage": "done", "progress": 1.0})
        return {
            "files_processed": 1, "files_skipped": 0,
            "by_type": {ftype: {"ok": 1, "error": 0}},
            "content": [{"file": os.path.basename(path), "type": ftype, "chars": len(text), "text": text[:5000]}],
            "errors": [], "summary": None,
        }

    if not os.path.isdir(path):
        return {"error": f"Not a file or directory: {path}"}

    # Parse include_types filter
    all_types = {"text", "pdf", "audio", "video", "image", "office"}
    if include_types:
        active_types = {t.strip().lower() for t in include_types.split(",") if t.strip().lower() in all_types}
    else:
        active_types = all_types

    # Resolve real path for symlink escape prevention
    real_root = os.path.realpath(path)

    # Collect candidates
    candidates: list[tuple[str, str, int]] = []  # (fpath, ftype, fsize)
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if len(candidates) >= max_files:
                break
            ext = os.path.splitext(fname)[1].lower()
            ftype = _extractors.get_file_type(ext)
            if not ftype or ftype not in active_types:
                continue
            fpath = os.path.join(dirpath, fname)
            # Symlink escape prevention
            if not os.path.realpath(fpath).startswith(real_root):
                continue
            try:
                fsize = os.path.getsize(fpath)
            except OSError:
                continue
            if fsize > max_size or fsize > _MAX_FILE_SIZE or fsize == 0:
                continue
            candidates.append((fpath, ftype, fsize))
        if len(candidates) >= max_files:
            break

    progress({"stage": "scanning", "progress": 0.1, "candidates": len(candidates)})

    # Process files in parallel
    results: dict = {"files_processed": 0, "files_skipped": 0, "by_type": {}, "content": [], "errors": []}

    def _process(item: tuple[str, str, int]) -> tuple[str, str, str, int, str | None]:
        fpath, ftype, fsize = item
        rel = os.path.relpath(fpath, path)
        text, err = _extractors.extract(fpath, whisper_model, whisper_device)
        if err or not text or len(text.strip()) < 20:
            return ("skip", rel, ftype, 0, err or "No extractable content")
        return ("ok", rel, ftype, len(text), text[:3000])

    total = len(candidates)
    done = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_process, item): item for item in candidates}
        for future in as_completed(futures):
            status, rel, ftype, chars, detail = future.result()
            results["by_type"].setdefault(ftype, {"ok": 0, "skip": 0, "error": 0})
            if status == "ok":
                results["files_processed"] += 1
                results["by_type"][ftype]["ok"] += 1
                results["content"].append({"file": rel, "type": ftype, "chars": chars, "text": detail or ""})
            else:
                results["files_skipped"] += 1
                results["by_type"][ftype]["skip"] += 1
                if detail and "not installed" in detail.lower():
                    results["errors"].append(f"{rel}: {detail}")
            done += 1
            if done % 10 == 0:
                progress({"stage": "extracting", "progress": 0.1 + 0.8 * (done / total)})

    # Optional summarization
    if summarize and results["content"]:
        progress({"stage": "summarizing", "progress": 0.95})
        combined = "\n\n".join(
            f"### {c['file']} ({c['type']})\n{c['text'][:1000]}"
            for c in results["content"][:20]
        )
        results["summary"] = ollama.summarize_text(combined[:12000])
    else:
        results["summary"] = None

    # Trim content text for output size (keep first 50 files' excerpts)
    results["content"] = results["content"][:50]
    results["errors"] = results["errors"][:30]

    progress({"stage": "done", "progress": 1.0})

    from .fetch import _log_event
    _log_event(path, results["files_processed"], "deep_ingest", extra={
        "total_candidates": total,
        "processed": results["files_processed"],
        "types": list(results["by_type"].keys()),
    })

    return results
