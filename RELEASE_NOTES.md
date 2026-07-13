# Local GPU Whisper Transcriber 1.0.0

Initial production installer release for Windows.

## Public Release Notes

- Public project links use `local-gpu-whisper-gui` so the repository is easier to find when searching for a Whisper GUI.
- The Windows setup package includes the updated repository URL used by the installer metadata.
- The README includes program features and basic usage instructions for file transcription, microphone transcription, logs, live transcript windows, CPU mode, and GPU mode.

## What's Included

- `LocalGPUWhisper-Setup.exe` all-in-one installer
- Local Whisper transcription GUI for microphone and audio-file input
- CPU/GPU toggle with automatic CPU fallback
- Static activity log, optional log window, and optional live transcript window
- Output file picker and open-output button
- Bundled Python application runtime dependencies
- Bundled NVIDIA CUDA 12/cuDNN runtime DLLs used by `ctranslate2`/`faster-whisper`
- Admin installer with folder selection, install progress, shortcuts, and Programs and Features uninstall entry

## Requirements

- Windows 10 or newer
- NVIDIA GPU driver installed separately for GPU mode
- Compatible NVIDIA GPU recommended for GPU transcription
- Internet access may be needed on first run to download the selected Whisper model

## Notes

- NVIDIA GPU drivers are not bundled.
- CUDA/cuDNN runtime DLLs required by the app backend are bundled with the installer.
- If GPU mode is unavailable, the app should log the issue and fall back to CPU.

## Warranty

This software is provided as-is with zero warranties. Carlson Data Center and contributors are not responsible for transcription accuracy, data loss, hardware issues, driver issues, or any damages from using the software.
