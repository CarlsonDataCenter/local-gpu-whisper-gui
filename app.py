import os
import importlib
import json
import queue
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QDoubleSpinBox,
    QCheckBox,
)

try:
    from faster_whisper import WhisperModel
except Exception as exc:  # pragma: no cover - import-time UI fallback
    WhisperModel = None
    WHISPER_IMPORT_ERROR = exc
else:
    WHISPER_IMPORT_ERROR = None


TARGET_SAMPLE_RATE = 16000
REQUIRED_CUDA_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")
_SOUNDDEVICE_MODULE = None
_SOUNDDEVICE_ERROR: Optional[Exception] = None


def resource_path(*parts: str) -> Path:
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_dir.joinpath(*parts)


def app_icon() -> QIcon:
    icon_path = resource_path("assets", "app.ico")
    return QIcon(str(icon_path)) if icon_path.exists() else QIcon()


@dataclass
class AudioDevice:
    index: int
    name: str
    channels: int
    default_samplerate: float


def list_input_devices() -> List[AudioDevice]:
    sd = _load_sounddevice()
    devices = []
    for idx, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            devices.append(
                AudioDevice(
                    index=idx,
                    name=device["name"],
                    channels=int(device["max_input_channels"]),
                    default_samplerate=float(device["default_samplerate"] or TARGET_SAMPLE_RATE),
                )
            )
    return devices


def format_device_label(device: AudioDevice) -> str:
    sr = int(round(device.default_samplerate))
    return f"{device.name} (#{device.index}, {device.channels} ch, {sr} Hz)"


def _sounddevice_runtime_dirs() -> List[Path]:
    candidates = []
    for runtime_root in _runtime_roots():
        candidates.extend(
            [
                runtime_root,
                runtime_root / "av.libs",
                runtime_root / "_sounddevice_data" / "portaudio-binaries",
            ]
        )
    return [path for path in candidates if path.exists()]


def _load_sounddevice():
    global _SOUNDDEVICE_MODULE, _SOUNDDEVICE_ERROR
    if _SOUNDDEVICE_MODULE is not None:
        return _SOUNDDEVICE_MODULE
    if _SOUNDDEVICE_ERROR is not None:
        raise RuntimeError(f"sounddevice is unavailable: {_SOUNDDEVICE_ERROR}") from _SOUNDDEVICE_ERROR

    dll_dirs = []
    try:
        for runtime_dir in _sounddevice_runtime_dirs():
            dll_dirs.append(os.add_dll_directory(str(runtime_dir)))
        _SOUNDDEVICE_MODULE = importlib.import_module("sounddevice")
        return _SOUNDDEVICE_MODULE
    except Exception as exc:
        _SOUNDDEVICE_ERROR = exc
        raise RuntimeError(f"sounddevice is unavailable: {exc}") from exc
    finally:
        for handle in dll_dirs:
            try:
                handle.close()
            except Exception:
                pass


def resample_audio(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if audio.size == 0 or source_rate == target_rate:
        return audio.astype(np.float32, copy=False)

    duration_seconds = len(audio) / float(source_rate)
    target_length = max(1, int(round(duration_seconds * target_rate)))
    source_positions = np.linspace(0.0, 1.0, num=len(audio), endpoint=False, dtype=np.float32)
    target_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False, dtype=np.float32)
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def _dll_exists(folder: Path, filename: str) -> bool:
    try:
        return (folder / filename).exists()
    except Exception:
        return False


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _runtime_roots() -> List[Path]:
    roots = [_runtime_root()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(_runtime_root() / "_internal")

    seen = set()
    unique_roots: List[Path] = []
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        key = str(resolved).lower()
        if key not in seen and resolved.exists():
            seen.add(key)
            unique_roots.append(resolved)
    return unique_roots


def find_cuda_runtime_dirs(preferred: Optional[Path] = None) -> Tuple[List[Path], List[str]]:
    candidates: List[Path] = []
    notes: List[str] = []
    discovered_versions: List[str] = []

    if preferred:
        candidates.append(preferred)

    for env_name in ("CUDA_PATH", "CUDA_PATH_V12_0", "CUDA_PATH_V12_1", "CUDA_PATH_V12_2", "CUDA_PATH_V13_0"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(Path(env_value) / "bin" / "x64")
            candidates.append(Path(env_value) / "bin")

    cuda_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if cuda_root.exists():
        for version_dir in sorted(cuda_root.glob("v*")):
            discovered_versions.append(version_dir.name)
            candidates.append(version_dir / "bin" / "x64")
            candidates.append(version_dir / "bin")

    for runtime_root in _runtime_roots():
        candidates.extend(
            [
                runtime_root,
                runtime_root / "nvidia" / "cublas" / "bin",
                runtime_root / "nvidia" / "cudnn" / "bin",
                runtime_root / "ctranslate2",
            ]
        )

    seen = set()
    runtime_dirs: List[Path] = []
    found: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        present = [dll for dll in REQUIRED_CUDA_DLLS if _dll_exists(candidate, dll)]
        if present:
            notes.append(f"Found {', '.join(present)} in {candidate}, but not all required DLLs.")
            runtime_dirs.append(candidate)
            found.update(present)
        if found.issuperset(REQUIRED_CUDA_DLLS):
            break

    if discovered_versions:
        notes.append(
            "Detected CUDA install(s): " + ", ".join(discovered_versions) + ". "
            "This Whisper backend currently needs CUDA 12 cuBLAS (cublas64_12.dll) and cuDNN 9 (cudnn64_9.dll)."
        )
    if not runtime_dirs and found:
        runtime_dirs = [candidate for candidate in candidates if any(_dll_exists(candidate, dll) for dll in REQUIRED_CUDA_DLLS)]
    return runtime_dirs, notes


class WhisperTranscriber:
    def __init__(
        self,
        model_name: str,
        use_gpu: bool = True,
        cuda_runtime_dir: Optional[Path] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ):
        self.model_name = model_name
        self.use_gpu = use_gpu
        self.cuda_runtime_dir = cuda_runtime_dir
        self.status_callback = status_callback
        self.model: Optional[WhisperModel] = None
        self._dll_dirs: List[object] = []

    def _log(self, message: str) -> None:
        if self.status_callback:
            self.status_callback(message)

    def _activate_cuda_runtime(self) -> bool:
        runtime_dirs, notes = find_cuda_runtime_dirs(self.cuda_runtime_dir)
        if not runtime_dirs:
            if notes:
                raise RuntimeError(
                    "CUDA runtime was not found for this backend.\n"
                    + "\n".join(notes)
                    + "\nThis app currently needs CUDA 12 cuBLAS + cuDNN 9 for GPU inference."
                )
            raise RuntimeError(
                "CUDA runtime was not found.\n"
                "This app currently needs CUDA 12 cuBLAS + cuDNN 9 for GPU inference."
            )

        try:
            for runtime_dir in runtime_dirs:
                self._dll_dirs.append(os.add_dll_directory(str(runtime_dir)))
            existing_path = os.environ.get("PATH", "")
            runtime_path_text = [str(runtime_dir) for runtime_dir in runtime_dirs]
            os.environ["PATH"] = os.pathsep.join(runtime_path_text + [existing_path])
            self._log("CUDA runtime paths loaded: " + ", ".join(str(path) for path in runtime_dirs))
            return True
        except Exception as exc:
            raise RuntimeError(f"Found CUDA runtime paths, but could not activate them: {exc}") from exc

    def load(self) -> None:
        if WhisperModel is None:
            raise RuntimeError(f"faster-whisper is unavailable: {WHISPER_IMPORT_ERROR}")

        if self.model is not None:
            return

        self._log(f"Loading Whisper model '{self.model_name}'")
        if self.use_gpu:
            self._log("Trying GPU backend")
            self._activate_cuda_runtime()
            try:
                self.model = WhisperModel(self.model_name, device="cuda", compute_type="float16")
                self._log("Whisper model ready on GPU")
                return
            except Exception as exc:
                self.use_gpu = False
                self._log(f"GPU init failed, falling back to CPU: {exc}")
                self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                self._log("Whisper model ready on CPU")
                return

        self.model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        self._log("Whisper model ready on CPU")

    def _transcribe_once(self, audio, language: str) -> Tuple[str, List[Tuple[float, float, str]]]:
        self.load()
        self._log("Whisper running")
        segments, _info = self.model.transcribe(
            audio,
            language=None if language == "Auto" else language,
            vad_filter=True,
            beam_size=5,
        )
        text_parts = []
        detail_rows = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                text_parts.append(text)
                detail_rows.append((segment.start, segment.end, text))
                self._log(f"Chunk segment: {segment.start:.1f}-{segment.end:.1f}s")
        return " ".join(text_parts).strip(), detail_rows

    @staticmethod
    def _looks_like_cuda_runtime_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "cublas64_12.dll",
                "cuda",
                "cudnn",
                "gpu",
                "device not found",
                "no cuda",
                "failed to load library",
            )
        )

    def transcribe(self, audio, language: str) -> Tuple[str, List[Tuple[float, float, str]], bool]:
        try:
            text, details = self._transcribe_once(audio, language)
            self._log("Whisper finished chunk")
            return text, details, False
        except Exception as exc:
            if self.use_gpu and self._looks_like_cuda_runtime_error(exc):
                self.model = None
                self.use_gpu = False
                self._log(f"GPU runtime error detected, retrying on CPU: {exc}")
                text, details = self._transcribe_once(audio, language)
                self._log("Whisper finished chunk on CPU fallback")
                return text, details, True
            raise


class TranscriptionWorker(threading.Thread):
    def __init__(
        self,
        device_index: int,
        sample_rate: int,
        chunk_seconds: float,
        language: str,
        model_name: str,
        output_path: Optional[Path],
        cuda_runtime_dir: Optional[Path],
        prefer_gpu: bool,
        event_queue: "queue.Queue[tuple]",
    ):
        super().__init__(daemon=True)
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.chunk_seconds = chunk_seconds
        self.language = language
        self.model_name = model_name
        self.output_path = output_path
        self.cuda_runtime_dir = cuda_runtime_dir
        self.prefer_gpu = prefer_gpu
        self.event_queue = event_queue
        self.stop_event = threading.Event()
        self.frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self.transcriber = WhisperTranscriber(
            model_name=model_name,
            use_gpu=prefer_gpu,
            cuda_runtime_dir=cuda_runtime_dir,
            status_callback=self._emit_status,
        )
        self.stream = None

    def stop(self) -> None:
        self.stop_event.set()

    def _emit_status(self, message: str) -> None:
        self._emit("status", message)

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            self.event_queue.put(("status", f"Audio callback: {status}"))
        block = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            self.frames.put_nowait(block)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(block)
            except queue.Full:
                pass

    def _emit(self, kind: str, payload) -> None:
        self.event_queue.put((kind, payload))

    def _append_output_file(self, text: str) -> None:
        if not self.output_path:
            return
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(text.rstrip() + "\n")
        except Exception as exc:
            self._emit("error", f"Failed to write transcript file: {exc}")
            self.stop_event.set()

    def _prepare_output_file(self) -> None:
        if not self.output_path:
            return
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.output_path.exists():
                self.output_path.touch()
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%H:%M:%S')}] Transcription started\n")
        except Exception as exc:
            self._emit("error", f"Failed to create transcript file: {exc}")
            self.stop_event.set()

    def run(self) -> None:
        self._emit("status", "Initializing microphone transcription worker")
        self._prepare_output_file()
        try:
            self.transcriber.load()
        except Exception as exc:
            self._emit("error", f"Failed to load Whisper model: {exc}")
            return

        try:
            sd = _load_sounddevice()
        except Exception as exc:
            self._emit("error", f"Failed to initialize audio input backend: {exc}")
            return

        open_errors = []
        for samplerate in (self.sample_rate, TARGET_SAMPLE_RATE):
            try:
                self.sample_rate = samplerate
                self.stream = sd.InputStream(
                    device=self.device_index,
                    channels=1,
                    samplerate=self.sample_rate,
                    dtype="float32",
                    callback=self._audio_callback,
                    blocksize=max(1024, int(self.sample_rate / 10)),
                )
                self.stream.start()
                break
            except Exception as exc:
                open_errors.append(f"{samplerate} Hz: {exc}")
                self.stream = None
        else:
            self._emit("error", "Failed to open input device. Tried:\n" + "\n".join(open_errors))
            return

        self._emit("status", "Recording started")
        chunk_samples = max(1, int(self.sample_rate * self.chunk_seconds))
        pending: List[np.ndarray] = []
        pending_samples = 0

        try:
            while not self.stop_event.is_set():
                try:
                    block = self.frames.get(timeout=0.25)
                except queue.Empty:
                    continue

                pending.append(block)
                pending_samples += len(block)

                if pending_samples < chunk_samples:
                    continue

                audio = np.concatenate(pending, axis=0)
                pending.clear()
                pending_samples = 0

                if audio.size == 0:
                    continue

                processed = resample_audio(audio, self.sample_rate, TARGET_SAMPLE_RATE)
                self._emit("status", f"Transcribing {len(processed) / TARGET_SAMPLE_RATE:.1f}s of audio...")
                start = time.time()
                try:
                    text, details, used_cpu = self.transcriber.transcribe(processed, self.language)
                except Exception as exc:
                    self._emit("error", f"Transcription failed: {exc}")
                    self.stop_event.set()
                    break
                elapsed = time.time() - start

                if used_cpu:
                    self._emit("status", "CUDA runtime unavailable, switched to CPU fallback")

                if text:
                    timestamp = time.strftime("%H:%M:%S")
                    self._emit("result", (timestamp, text, details, elapsed))
                    self._append_output_file(f"[{timestamp}] {text}")
                else:
                    self._emit("status", f"No speech detected in last chunk ({elapsed:.1f}s)")
        finally:
            if self.stream is not None:
                try:
                    self.stream.stop()
                except Exception:
                    pass
                try:
                    self.stream.close()
                except Exception:
                    pass
            self._emit("status", "Recording stopped")


class FileTranscriptionWorker(threading.Thread):
    def __init__(
        self,
        audio_path: Path,
        language: str,
        model_name: str,
        output_path: Optional[Path],
        cuda_runtime_dir: Optional[Path],
        prefer_gpu: bool,
        event_queue: "queue.Queue[tuple]",
    ):
        super().__init__(daemon=True)
        self.audio_path = audio_path
        self.language = language
        self.model_name = model_name
        self.output_path = output_path
        self.cuda_runtime_dir = cuda_runtime_dir
        self.prefer_gpu = prefer_gpu
        self.event_queue = event_queue
        self.transcriber = WhisperTranscriber(
            model_name=model_name,
            use_gpu=prefer_gpu,
            cuda_runtime_dir=cuda_runtime_dir,
            status_callback=self._emit_status,
        )

    def _emit(self, kind: str, payload) -> None:
        self.event_queue.put((kind, payload))

    def _emit_status(self, message: str) -> None:
        self._emit("status", message)

    def _append_output_file(self, text: str) -> None:
        if not self.output_path:
            return
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(text.rstrip() + "\n")
        except Exception as exc:
            self._emit("error", f"Failed to write transcript file: {exc}")

    def _prepare_output_file(self) -> None:
        if not self.output_path:
            return
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.output_path.exists():
                self.output_path.touch()
            with self.output_path.open("a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%H:%M:%S')}] Transcription started\n")
        except Exception as exc:
            self._emit("error", f"Failed to create transcript file: {exc}")

    def run(self) -> None:
        if not self.audio_path.exists():
            self._emit("error", f"Audio file not found: {self.audio_path}")
            return

        self._emit("status", f"Initializing file transcription worker for {self.audio_path.name}")
        self._prepare_output_file()
        try:
            self.transcriber.load()
        except Exception as exc:
            self._emit("error", f"Failed to load Whisper model: {exc}")
            return

        try:
            self._emit("status", f"Transcribing file: {self.audio_path.name}")
            start = time.time()
            text, details, used_cpu = self.transcriber.transcribe(str(self.audio_path), self.language)
            elapsed = time.time() - start

            if used_cpu:
                self._emit("status", "CUDA runtime unavailable, switched to CPU fallback")

            if text:
                timestamp = time.strftime("%H:%M:%S")
                self._emit("result", (timestamp, text, details, elapsed))
                self._append_output_file(f"[{timestamp}] {text}")
            else:
                self._emit("status", f"No speech detected in file ({elapsed:.1f}s)")
        except Exception as exc:
            self._emit("error", f"Transcription failed: {exc}")
        finally:
            self._emit("status", "File transcription finished")


class TextWindow(QMainWindow):
    def __init__(self, title: str, placeholder: str = ""):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self.resize(900, 500)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText(placeholder)
        self.setCentralWidget(self.text_edit)

    def append_line(self, text: str) -> None:
        self.text_edit.appendPlainText(text)

    def clear_text(self) -> None:
        self.text_edit.clear()

    def set_text(self, text: str) -> None:
        self.text_edit.setPlainText(text)

    def copy_all(self) -> None:
        self.text_edit.selectAll()
        self.text_edit.copy()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local GPU Whisper Transcriber")
        self.setWindowIcon(app_icon())
        self.resize(1100, 800)

        self.event_queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.input_devices: List[AudioDevice] = []
        self.device_map = {}
        self.console_window = TextWindow("Console Log", "Runtime events and status messages appear here.")
        self.transcript_window = TextWindow("Live Transcript", "Transcription output appears here in realtime.")
        self.console_window.hide()
        self.transcript_window.hide()

        self.input_mode = "mic"
        self.device_combo = QComboBox()
        self.audio_file_edit = QLineEdit()
        self.output_file_edit = QLineEdit(str(Path.cwd() / "transcript.txt"))
        self.model_combo = QComboBox()
        self.language_combo = QComboBox()
        self.chunk_spin = QDoubleSpinBox()
        self.prefer_gpu_checkbox = QCheckBox("Use GPU")
        self.status_label = QLabel("Ready")
        self.activity_log = QPlainTextEdit()
        self.start_button = QPushButton("Start transcription")
        self.stop_button = QPushButton("Stop")
        self.show_console_button = QPushButton("Show logs")
        self.show_transcript_button = QPushButton("Show transcript")
        self.open_output_button = QPushButton("Open output file")

        self._build_ui()
        self.refresh_devices()
        self._sync_input_mode()

        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll_events)
        self.timer.start()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Local GPU Whisper Transcription")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        subtitle = QLabel("Choose a microphone or audio file, then stream transcripts to the text box or an output file.")
        subtitle.setStyleSheet("color: #555;")
        root.addWidget(title)
        root.addWidget(subtitle)

        settings = QFrame()
        settings.setFrameShape(QFrame.StyledPanel)
        settings_layout = QGridLayout(settings)
        settings_layout.setColumnStretch(1, 1)
        settings_layout.setHorizontalSpacing(10)
        settings_layout.setVerticalSpacing(8)

        mode_label = QLabel("Input type")
        mic_radio = QRadioButton("Microphone")
        file_radio = QRadioButton("Audio file")
        mic_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(mic_radio)
        self.mode_group.addButton(file_radio)
        self.mode_group.idClicked.connect(self._on_mode_changed)
        mode_row = QHBoxLayout()
        mode_row.addWidget(mic_radio)
        mode_row.addWidget(file_radio)
        mode_row.addStretch(1)

        self.device_combo.setEditable(False)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_devices)

        browse_audio_button = QPushButton("Browse")
        browse_audio_button.clicked.connect(self.browse_audio_file)
        self.audio_file_edit.setPlaceholderText("Choose an audio file...")

        self.model_combo.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.model_combo.setCurrentText("small")

        self.language_combo.addItems(["Auto", "en", "es", "fr", "de", "it", "pt", "nl", "ja", "ko", "zh"])
        self.language_combo.setCurrentText("Auto")
        self.prefer_gpu_checkbox.setChecked(True)

        self.chunk_spin.setRange(2.0, 30.0)
        self.chunk_spin.setSingleStep(0.5)
        self.chunk_spin.setValue(5.0)
        self.chunk_spin.setSuffix(" s")

        output_browse_button = QPushButton("Browse")
        output_browse_button.clicked.connect(self.browse_output_file)

        settings_layout.addWidget(mode_label, 0, 0)
        settings_layout.addLayout(mode_row, 0, 1, 1, 2)
        settings_layout.addWidget(QLabel("Input device"), 1, 0)
        settings_layout.addWidget(self.device_combo, 1, 1)
        settings_layout.addWidget(refresh_button, 1, 2)
        settings_layout.addWidget(QLabel("Audio file"), 2, 0)
        settings_layout.addWidget(self.audio_file_edit, 2, 1)
        settings_layout.addWidget(browse_audio_button, 2, 2)
        settings_layout.addWidget(QLabel("Model"), 3, 0)
        settings_layout.addWidget(self.model_combo, 3, 1)
        settings_layout.addWidget(QLabel("Language"), 4, 0)
        settings_layout.addWidget(self.language_combo, 4, 1)
        settings_layout.addWidget(QLabel("Chunk size"), 5, 0)
        settings_layout.addWidget(self.chunk_spin, 5, 1)
        settings_layout.addWidget(self.prefer_gpu_checkbox, 5, 2)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Transcript file"))
        output_row.addWidget(self.output_file_edit, 1)
        output_row.addWidget(output_browse_button)

        root.addWidget(settings)
        root.addLayout(output_row)

        button_row = QHBoxLayout()
        self.start_button.clicked.connect(self.start_transcription)
        self.stop_button.clicked.connect(self.stop_transcription)
        self.show_console_button.clicked.connect(self.show_console_window)
        self.show_transcript_button.clicked.connect(self.show_transcript_window)
        self.open_output_button.clicked.connect(self.open_output_file)
        copy_button = QPushButton("Copy transcript")
        copy_button.clicked.connect(self.copy_transcript)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_transcript)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.show_console_button)
        button_row.addWidget(self.show_transcript_button)
        button_row.addWidget(self.open_output_button)
        button_row.addWidget(copy_button)
        button_row.addWidget(clear_button)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self.stop_button.setEnabled(False)
        self.open_output_button.setEnabled(False)

        footer = QLabel("Tip: smaller models are faster; larger ones are more accurate if your GPU has room.")
        footer.setStyleSheet("color: #666;")
        root.addWidget(footer)

        self.status_label.setStyleSheet("font-weight: 600;")
        root.addWidget(self.status_label)

        activity_label = QLabel("Activity")
        activity_label.setStyleSheet("font-weight: 600;")
        self.activity_log.setReadOnly(True)
        self.activity_log.setMaximumHeight(170)
        self.activity_log.setPlaceholderText("Start and runtime messages appear here.")
        root.addWidget(activity_label)
        root.addWidget(self.activity_log)

        brand_label = QLabel("Carlson Data Center x Codex")
        brand_label.setAlignment(Qt.AlignCenter)
        brand_label.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(brand_label)

    def _on_mode_changed(self, _id: int) -> None:
        buttons = self.mode_group.buttons()
        checked = next((button for button in buttons if button.isChecked()), None)
        if checked is not None:
            self.input_mode = "file" if checked.text() == "Audio file" else "mic"
        self._sync_input_mode()

    def _sync_input_mode(self) -> None:
        mic_enabled = self.input_mode == "mic"
        self.device_combo.setEnabled(mic_enabled)
        self.audio_file_edit.setEnabled(not mic_enabled)
        self.status_label.setText("Microphone mode" if mic_enabled else "Audio file mode")

    def show_console_window(self) -> None:
        self.console_window.show()
        self.console_window.raise_()
        self.console_window.activateWindow()
        self.log_event("Opened console log window")

    def show_transcript_window(self) -> None:
        self.transcript_window.show()
        self.transcript_window.raise_()
        self.transcript_window.activateWindow()
        self.log_event("Opened live transcript window")

    def refresh_devices(self) -> None:
        try:
            self.input_devices = list_input_devices()
        except Exception as exc:
            QMessageBox.critical(self, "Device error", f"Could not enumerate audio devices:\n\n{exc}")
            self.input_devices = []
            self.device_combo.clear()
            return

        self.device_map = {format_device_label(device): device for device in self.input_devices}
        self.device_combo.clear()
        self.device_combo.addItems(list(self.device_map.keys()))
        if self.device_combo.count() > 0:
            self.device_combo.setCurrentIndex(0)
            self.status_label.setText(f"Found {self.device_combo.count()} input device(s)")
        else:
            self.status_label.setText("No input devices found")

    def browse_output_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose transcript output file",
            str(Path(self.output_file_edit.text() or Path.cwd() / "transcript.txt")),
            "Text files (*.txt);;All files (*.*)",
        )
        if path:
            self.output_file_edit.setText(path)

    def browse_audio_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio file",
            str(Path.cwd()),
            "Audio files (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.mp4);;All files (*.*)",
        )
        if path:
            self.audio_file_edit.setText(path)

    def _touch_output_file(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not output_path.exists():
            output_path.touch()
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%H:%M:%S')}] Start accepted by GUI\n")
        self.open_output_button.setEnabled(True)
        self.log_event(f"Output file is ready: {output_path}")

    def start_transcription(self) -> None:
        try:
            self._start_transcription_impl()
        except Exception as exc:
            details = traceback.format_exc()
            self.status_label.setText("Start failed")
            self.log_event(f"START ERROR: {exc}")
            self.log_event(details)
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            QMessageBox.critical(self, "Start error", f"Could not start transcription:\n\n{exc}")

    def _start_transcription_impl(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        if WhisperModel is None:
            QMessageBox.critical(
                self,
                "Missing dependency",
                "faster-whisper is not installed or failed to import.\n\n"
                f"Import error: {WHISPER_IMPORT_ERROR}\n\n"
                "Install the requirements and try again.",
            )
            return

        self.status_label.setText("Initializing transcription...")
        self.log_event("Start button clicked")

        output_text = self.output_file_edit.text().strip()
        output_path = Path(output_text) if output_text else None
        cuda_runtime_dir = None
        detected_cuda, notes = find_cuda_runtime_dirs()
        if detected_cuda:
            self.log_event("Auto-detected CUDA runtime paths: " + ", ".join(str(path) for path in detected_cuda))
        elif notes:
            for note in notes:
                self.log_event(note)
            self.log_event("CUDA 13 was found, but this backend needs CUDA 12 cuBLAS + cuDNN 9.")

        self.log_event(
            "Launch request: "
            + f"mode={self.input_mode}, "
            + f"model={self.model_combo.currentText()}, "
            + f"language={self.language_combo.currentText()}, "
            + f"gpu={'on' if self.prefer_gpu_checkbox.isChecked() else 'off'}"
        )

        if self.input_mode == "file":
            audio_text = self.audio_file_edit.text().strip()
            if not audio_text:
                QMessageBox.warning(self, "Missing file", "Please choose an audio file first.")
                return
            audio_path = Path(audio_text)
            self.log_event(f"Starting file transcription: {audio_path}")
            if output_path:
                self._touch_output_file(output_path)
            self.worker = FileTranscriptionWorker(
                audio_path=audio_path,
                language=self.language_combo.currentText(),
                model_name=self.model_combo.currentText(),
                output_path=output_path,
                cuda_runtime_dir=cuda_runtime_dir,
                prefer_gpu=self.prefer_gpu_checkbox.isChecked(),
                event_queue=self.event_queue,
            )
        else:
            device_label = self.device_combo.currentText().strip()
            if not device_label:
                QMessageBox.warning(self, "Missing device", "Please choose an input device first.")
                return
            device = self.device_map[device_label]
            self.log_event(f"Starting microphone transcription on {device_label}")
            if output_path:
                self._touch_output_file(output_path)
            self.worker = TranscriptionWorker(
                device_index=device.index,
                sample_rate=max(1, int(round(device.default_samplerate)) or TARGET_SAMPLE_RATE),
                chunk_seconds=float(self.chunk_spin.value()),
                language=self.language_combo.currentText(),
                model_name=self.model_combo.currentText(),
                output_path=output_path,
                cuda_runtime_dir=cuda_runtime_dir,
                prefer_gpu=self.prefer_gpu_checkbox.isChecked(),
                event_queue=self.event_queue,
            )

        try:
            self.worker.start()
        except Exception as exc:
            self.worker = None
            QMessageBox.critical(self, "Start error", f"Could not start transcription:\n\n{exc}")
            self.log_event(f"ERROR: could not start worker: {exc}")
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status_label.setText("Starting transcription...")
        backend = "GPU" if self.prefer_gpu_checkbox.isChecked() else "CPU"
        self.log_event(f"Started transcription ({backend} preferred)")

    def stop_transcription(self) -> None:
        if self.worker and self.worker.is_alive():
            stop = getattr(self.worker, "stop", None)
            if callable(stop):
                stop()
            self.status_label.setText("Stopping...")
            self.log_event("Stopping transcription")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def copy_transcript(self) -> None:
        text = self.transcript_window.text_edit.toPlainText().strip()
        QApplication.clipboard().setText(text)
        self.status_label.setText("Transcript copied to clipboard")
        self.log_event("Copied transcript to clipboard")

    def clear_transcript(self) -> None:
        self.transcript_window.clear_text()
        self.status_label.setText("Transcript cleared")
        self.log_event("Cleared transcript window")

    def open_output_file(self) -> None:
        output_text = self.output_file_edit.text().strip()
        if not output_text:
            QMessageBox.information(self, "No output file", "Choose a transcript output file first.")
            return

        output_path = Path(output_text)
        if not output_path.exists():
            QMessageBox.information(self, "Output file not found", f"The output file does not exist yet:\n\n{output_path}")
            return

        try:
            os.startfile(str(output_path))  # type: ignore[attr-defined]
            self.log_event(f"Opened output file: {output_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Open output file", f"Could not open output file:\n\n{exc}")
            self.log_event(f"ERROR: could not open output file: {exc}")

    def _append_transcript(self, timestamp: str, text: str, details: List[Tuple[float, float, str]], elapsed: float) -> None:
        self.transcript_window.append_line(f"[{timestamp}] {text}")
        for start, end, segment_text in details:
            self.transcript_window.append_line(f"    {start:6.1f}-{end:6.1f}s  {segment_text}")
        self.transcript_window.append_line(f"    processed in {elapsed:.2f}s")
        self.transcript_window.append_line("")
        self.transcript_window.text_edit.verticalScrollBar().setValue(
            self.transcript_window.text_edit.verticalScrollBar().maximum()
        )

    def log_event(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.console_window.append_line(line)
        self.activity_log.appendPlainText(line)
        self.console_window.text_edit.verticalScrollBar().setValue(
            self.console_window.text_edit.verticalScrollBar().maximum()
        )
        self.activity_log.verticalScrollBar().setValue(self.activity_log.verticalScrollBar().maximum())

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.event_queue.get_nowait()
                if kind == "status":
                    self.status_label.setText(str(payload))
                    self.log_event(str(payload))
                elif kind == "result":
                    timestamp, text, details, elapsed = payload
                    self._append_transcript(timestamp, text, details, elapsed)
                    self.status_label.setText(f"Last chunk: {len(text)} chars in {elapsed:.2f}s")
                    self.log_event(f"Transcribed {len(text)} chars in {elapsed:.2f}s")
                elif kind == "error":
                    QMessageBox.critical(self, "Transcription error", str(payload))
                    self.status_label.setText("Error")
                    self.log_event(f"ERROR: {payload}")
                    self.start_button.setEnabled(True)
                    self.stop_button.setEnabled(False)
        except queue.Empty:
            pass
        finally:
            if self.worker and not self.worker.is_alive():
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker and self.worker.is_alive():
            stop = getattr(self.worker, "stop", None)
            if callable(stop):
                stop()
        self.console_window.close()
        self.transcript_window.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication([])
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    app.exec()


def run_start_smoke_test(result_path: Path) -> None:
    class DummyWorker:
        def __init__(self, *args, **kwargs):
            self.started = False

        def start(self):
            self.started = True

        def is_alive(self):
            return self.started

    qapp = QApplication([])
    window = MainWindow()
    smoke_dir = result_path.parent
    smoke_dir.mkdir(parents=True, exist_ok=True)
    audio_path = smoke_dir / "smoke-audio.wav"
    output_path = smoke_dir / "smoke-transcript.txt"
    audio_path.write_bytes(b"dummy")

    globals()["WhisperModel"] = object
    globals()["WHISPER_IMPORT_ERROR"] = None
    globals()["FileTranscriptionWorker"] = DummyWorker

    window.input_mode = "file"
    window._sync_input_mode()
    window.audio_file_edit.setText(str(audio_path))
    window.output_file_edit.setText(str(output_path))
    window.prefer_gpu_checkbox.setChecked(False)
    window.start_transcription()

    result = {
        "status": window.status_label.text(),
        "activity": window.activity_log.toPlainText().splitlines(),
        "output_exists": output_path.exists(),
        "output_text": output_path.read_text(encoding="utf-8").splitlines() if output_path.exists() else [],
    }
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    qapp.quit()


if __name__ == "__main__":
    if "--smoke-start" in sys.argv:
        output_arg = next((arg for arg in sys.argv if arg.startswith("--smoke-output=")), "")
        output_path = Path(output_arg.split("=", 1)[1]) if output_arg else Path.cwd() / "whisper-smoke-result.json"
        try:
            run_start_smoke_test(output_path)
        except BaseException as exc:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps({"error": str(exc), "traceback": traceback.format_exc()}, indent=2),
                encoding="utf-8",
            )
            raise
    else:
        main()
