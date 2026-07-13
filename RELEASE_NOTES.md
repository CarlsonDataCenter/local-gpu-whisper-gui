# Local GPU Whisper Transcriber 1.0.1

Maintenance release for the public GitHub repository.

## What's Changed

- Updated public project links from `local-gpu-whisper` to `local-gpu-whisper-gui` so the repository is easier to find when searching for a Whisper GUI.
- Rebuilt the Windows setup package with the updated repository URL used by the installer metadata.
- Expanded the README with program features and basic usage instructions for file transcription, microphone transcription, logs, live transcript windows, CPU mode, and GPU mode.
- Kept the same application functionality as the initial production build.

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
