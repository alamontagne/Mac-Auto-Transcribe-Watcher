"""
watcher.py — iCloud-aware MP3 file watcher for auto-transcription
Watches a directory for new .mp3 files, waits for iCloud to fully
download them, then runs transcribe.py and sends an iMessage on completion.

Usage: python3 watcher.py
"""

import os
import time
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ─────────────────────────────────────────────
# CONFIGURATION — edit these values
# ─────────────────────────────────────────────
WATCH_DIR = "/Users/alamontagne/Documents/Trancscribe"
SCRIPT    = "/Users/alamontagne/Documents/Trancscribe/transcribe.py"
IMESSAGE_TO = "+14163000385"   # Your own Apple ID email or phone number e.g. +15141234567
# ─────────────────────────────────────────────


def send_imessage(message: str) -> None:
    """Send an iMessage via osascript (no external apps required)."""
    applescript = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{IMESSAGE_TO}" of targetService
        send "{message}" to targetBuddy
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", applescript], check=True)
        print(f"[iMessage] Sent: {message}")
    except subprocess.CalledProcessError as e:
        print(f"[iMessage] Failed to send: {e}")


def wait_until_complete(filepath: str, stable_seconds: int = 5, timeout: int = 600) -> bool:
    """
    Wait until the file is fully downloaded from iCloud and its size stabilises.

    iCloud files can appear as tiny stubs before the full download completes.
    This function polls the file size every second and only returns True once
    the size has been non-zero and unchanged for `stable_seconds` consecutive checks.

    Args:
        filepath:       Full path to the file.
        stable_seconds: How many consecutive unchanged-size checks before we
                        consider the file complete (default: 5 seconds).
        timeout:        Maximum seconds to wait before giving up (default: 600 = 10 min).

    Returns:
        True if the file is ready, False if we timed out.
    """
    print(f"[Watcher] Waiting for iCloud download to complete: {os.path.basename(filepath)}")
    last_size   = -1
    stable_count = 0
    elapsed     = 0

    while elapsed < timeout:
        try:
            if not os.path.exists(filepath):
                # File not visible yet — keep waiting
                time.sleep(2)
                elapsed += 2
                continue

            current_size = os.path.getsize(filepath)

            if current_size > 0 and current_size == last_size:
                stable_count += 1
                if stable_count >= stable_seconds:
                    print(f"[Watcher] File ready ({current_size} bytes): {os.path.basename(filepath)}")
                    return True
            else:
                if stable_count > 0:
                    print(f"[Watcher] Size still changing ({last_size} → {current_size} bytes)…")
                stable_count = 0
                last_size    = current_size

        except OSError as e:
            print(f"[Watcher] OS error while checking file: {e}")

        time.sleep(1)
        elapsed += 1

    print(f"[Watcher] Timed out after {timeout}s waiting for: {os.path.basename(filepath)}")
    return False


class MP3Handler(FileSystemEventHandler):
    """Handles new file creation events in the watched directory."""

    def on_created(self, event):
        if event.is_directory:
            return

        filepath = event.src_path
        filename = os.path.basename(filepath)

        # Only process .mp3 files; ignore .icloud stubs, .txt outputs, etc.
        if not filename.lower().endswith(".mp3"):
            return

        print(f"\n[Watcher] ── New MP3 detected: {filename} ──")

        # Wait for iCloud to finish syncing the file
        if not wait_until_complete(filepath):
            msg = f"⚠️ Timed out waiting for {filename} to download from iCloud."
            print(f"[Watcher] {msg}")
            send_imessage(msg)
            return

        print(f"[Watcher] Starting transcription: {filename}")

        # Run transcribe.py with just the filename (cwd is set to WATCH_DIR)
        try:
            result = subprocess.run(
                ["python3", SCRIPT, filename],
                capture_output=True,
                text=True,
                cwd=WATCH_DIR
            )

            if result.returncode == 0:
                base     = os.path.splitext(filename)[0]
                txt_file = f"{base}.txt"
                msg = f"✅ Transcription complete: {txt_file}"
                print(f"[Watcher] {msg}")
                if result.stdout:
                    print(f"[transcribe.py stdout]\n{result.stdout.strip()}")
                send_imessage(msg)
            else:
                error_snippet = result.stderr.strip()[:200] if result.stderr else "unknown error"
                msg = f"❌ Transcription failed for {filename}: {error_snippet}"
                print(f"[Watcher] {msg}")
                send_imessage(msg)

        except Exception as e:
            msg = f"❌ Watcher exception on {filename}: {str(e)}"
            print(f"[Watcher] {msg}")
            send_imessage(msg)


if __name__ == "__main__":
    if not os.path.isdir(WATCH_DIR):
        raise SystemExit(f"[Watcher] ERROR: Watch directory does not exist: {WATCH_DIR}")

    print(f"[Watcher] Monitoring: {WATCH_DIR}")
    print(f"[Watcher] Will notify: {IMESSAGE_TO}")
    print(f"[Watcher] Press Ctrl+C to stop.\n")

    observer = Observer()
    observer.schedule(MP3Handler(), WATCH_DIR, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Watcher] Stopping…")
        observer.stop()
    observer.join()
    print("[Watcher] Stopped.")
