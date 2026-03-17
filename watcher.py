import os
import time
import logging
import subprocess
import shutil
import uuid
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ========================= CONFIG =========================
WATCH_DIR = "/Users/alamontagne/Documents/Trancscribe"
VENV_PYTHON = "/Users/alamontagne/whisperx-env/bin/python3"
SCRIPT_PATH = os.path.join(WATCH_DIR, "transcribe.py")
PHONE_NUMBER = "+14163000385"
TEMP_DIR = "/tmp/transcribe_processing"
# ========================================================

os.makedirs(TEMP_DIR, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(WATCH_DIR, "watcher.log"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Keyed by absolute path to avoid same-filename collisions across subfolders
processing_files = set()


def send_imessage(message: str):
    if len(message) > 400:
        message = message[:397] + "..."
    # Escape backslashes and double-quotes so osascript doesn't choke
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set targetService to first service whose service type = iMessage
        set targetBuddy to buddy "{PHONE_NUMBER}" of targetService
        send "{safe}" to targetBuddy
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], timeout=10)
        logging.info("iMessage sent")
    except Exception as e:
        logging.error(f"iMessage failed: {e}")


def load_hf_token():
    """
    Return HF_TOKEN from the environment if already present, otherwise
    source ~/.zshrc in a login shell and pull it from there.
    """
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["/bin/zsh", "-i", "-c", "source ~/.zshrc 2>/dev/null; echo $HF_TOKEN"],
            capture_output=True, text=True, timeout=10
        )
        token = result.stdout.strip()
        if token:
            logging.info("HF_TOKEN sourced from ~/.zshrc via login shell")
            return token
    except Exception as e:
        logging.warning(f"Could not source HF_TOKEN from ~/.zshrc: {e}")
    return None


def wait_for_file_ready(file_path, timeout=180, check_interval=2, stability_seconds=15):
    logging.info(f"Waiting for iCloud sync: {os.path.basename(file_path)}")
    start_time = time.time()
    last_size = -1
    stable_count = 0
    while time.time() - start_time < timeout:
        if not os.path.exists(file_path):
            time.sleep(check_interval)
            continue
        try:
            current_size = os.path.getsize(file_path)
            if current_size > 0 and current_size == last_size:
                stable_count += 1
                if stable_count >= stability_seconds:
                    logging.info("iCloud file is stable and ready")
                    return True
            else:
                stable_count = 0
                last_size = current_size
        except OSError:
            pass
        time.sleep(check_interval)
    logging.warning("Timed out waiting for iCloud file to stabilise")
    return False


def force_materialize(file_path, max_attempts=40):
    """Force iCloud to fully download the file by reading the first chunk."""
    logging.info("Forcing iCloud materialisation...")
    for _ in range(max_attempts):
        try:
            with open(file_path, 'rb') as f:
                f.read(4096)
            logging.info("File fully materialised")
            return True
        except OSError:
            time.sleep(1)
    return False


JOB_TIMEOUT = 14400  # 4 hours — kills only a genuinely hung process; run longer files manually

def drain_process(process, deadline):
    """
    Read stdout line-by-line in real time while enforcing a 4-hour wall-clock
    limit (set at Popen time, passed in as a deadline). Sufficient for any
    meeting-length recording; longer files should be run manually.
    """
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            process.kill()
            raise subprocess.TimeoutExpired(process.args, JOB_TIMEOUT)
        line = process.stdout.readline()
        if line:
            logging.info(f"TRANSCRIBE: {line.strip()}")
        elif process.poll() is not None:
            # Process finished — drain any remaining buffered output
            for extra in process.stdout:
                logging.info(f"TRANSCRIBE: {extra.strip()}")
            break
        else:
            time.sleep(0.1)
    return process.wait()


class MP3Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory or not event.src_path.lower().endswith(".mp3"):
            return

        audio_path = os.path.abspath(event.src_path)

        # Use the absolute path as the dedup key — not just the filename
        if audio_path in processing_files:
            logging.info(f"Already processing {audio_path} — skipping duplicate event")
            return
        processing_files.add(audio_path)

        filename = os.path.basename(audio_path)
        rel_parts = os.path.relpath(audio_path, WATCH_DIR).split(os.sep)

        # Determine mode from the immediate subfolder name
        mode = rel_parts[0] if len(rel_parts) > 1 else "group"
        if mode == "solo":
            num_speakers = 1
            mode_label = "Solo (1 speaker)"
        elif mode == "duo":
            num_speakers = 2
            mode_label = "Duo (2 speakers)"
        else:
            num_speakers = 0  # 0 = auto-detect in transcribe.py
            mode_label = "Group (auto-detect)"

        logging.info(f"New MP3 detected — {filename} | mode={mode} | speakers={num_speakers or 'auto'}")

        temp_path = None
        try:
            if not wait_for_file_ready(audio_path):
                send_imessage(f"❌ iCloud sync timed out for:\n{filename}\n\nThe file never fully downloaded. Try again.")
                return

            if not force_materialize(audio_path):
                send_imessage(f"❌ Could not unlock iCloud file:\n{filename}")
                return

            temp_path = os.path.join(TEMP_DIR, f"temp_{uuid.uuid4().hex[:8]}_{filename}")
            shutil.copy2(audio_path, temp_path)
            logging.info(f"Copied to local temp: {temp_path}")

            # --- Friendly "started" iMessage ---
            send_imessage(
                f"🎙️ Transcription started\n\n"
                f"File: {filename}\n"
                f"Mode: {mode_label}\n\n"
                f"I'll message you when it's done."
            )

            env = os.environ.copy()
            env["PATH"] = "/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

            hf_token = load_hf_token()
            if hf_token:
                env["HF_TOKEN"] = hf_token
            else:
                logging.error("HF_TOKEN not found — diarization will fail")
                send_imessage(f"❌ HF_TOKEN not set. Transcription cannot start for:\n{filename}")
                return

            logging.info("Launching transcription subprocess (4-hour limit)...")
            deadline = time.time() + JOB_TIMEOUT
            process = subprocess.Popen(
                [VENV_PYTHON, SCRIPT_PATH, temp_path, str(num_speakers)],
                cwd=WATCH_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            returncode = drain_process(process, deadline=deadline)

            # Move transcript back next to the original iCloud file
            temp_transcript = os.path.splitext(temp_path)[0] + "_transcript.txt"
            final_transcript = os.path.splitext(audio_path)[0] + "_transcript.txt"
            if os.path.exists(temp_transcript):
                shutil.move(temp_transcript, final_transcript)
                logging.info(f"Transcript saved to: {final_transcript}")

            if returncode == 0:
                send_imessage(
                    f"✅ Transcription complete!\n\n"
                    f"File: {filename}\n"
                    f"Mode: {mode_label}\n"
                    f"Transcript: {os.path.basename(final_transcript)}"
                )
                logging.info("Transcription succeeded")
            else:
                send_imessage(
                    f"❌ Transcription failed\n\n"
                    f"File: {filename}\n\n"
                    f"Check watcher.log for details."
                )
                logging.error(f"Transcription process exited with code {returncode}")

        except subprocess.TimeoutExpired:
            logging.error("Transcription hit the 4-hour limit — process killed")
            # Unlikely but possible: check if the transcript was written before the kill
            temp_transcript = os.path.splitext(temp_path)[0] + "_transcript.txt"
            final_transcript = os.path.splitext(audio_path)[0] + "_transcript.txt"
            if os.path.exists(temp_transcript):
                shutil.move(temp_transcript, final_transcript)
                logging.info("Transcript found after timeout — moved successfully")
                send_imessage(
                    f"✅ Transcription complete!\n\n"
                    f"File: {filename}\n"
                    f"Mode: {mode_label}\n"
                    f"Transcript: {os.path.basename(final_transcript)}\n\n"
                    f"(Note: ran right up to the 4-hour limit.)"
                )
            else:
                send_imessage(
                    f"⏰ Transcription timed out\n\n"
                    f"File: {filename}\n\n"
                    f"Exceeded the 4-hour limit. Run this file manually if needed."
                )
        except Exception as e:
            logging.error(f"Unexpected error processing {filename}: {e}", exc_info=True)
            send_imessage(f"❌ Unexpected error\n\nFile: {filename}\n\nCheck watcher.log for details.")
        finally:
            processing_files.discard(audio_path)
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                    logging.info("Temp file cleaned up")
                except Exception:
                    pass


if __name__ == "__main__":
    event_handler = MP3Handler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=True)
    observer.start()
    logging.info("Watcher started — monitoring for new MP3s (solo / duo / group subfolders)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
