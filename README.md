# Local GPU Whisper Transcriber

A small Windows desktop app for live microphone transcription or one-off audio-file transcription with local Whisper inference.

## Features

- Select a microphone or an audio file
- Choose a Whisper model size
- Toggle `Use GPU` on or off
- Auto-detect bundled CUDA 12 runtime DLLs when available
- Save transcripts to a text file while also showing them in the UI
- Copy or clear the live transcript
- Separate console and live transcript windows

## Setup

1. Create a virtual environment if you want one.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Run the app:

```powershell
python app.py
```

## Build EXE

If you have the bundled Python runtime used on this machine, you can build a standalone executable with:

```powershell
pyinstaller --noconsole --onefile --clean --name WhisperTranscriber --collect-all PySide6 --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all av --collect-all sounddevice app.py
```

## Notes

- First run may download the selected Whisper model.
- For best results, use a CUDA-capable NVIDIA GPU and a smaller model like `small` or `medium` for real-time use.
- The packaged build bundles the CUDA 12 runtime DLLs used by `ctranslate2`/`faster-whisper`, specifically `cublas64_12.dll` and `cudnn64_9.dll`.
- The file input mode accepts common audio formats. For microphone mode, the app captures at the device's rate and resamples before transcription.
