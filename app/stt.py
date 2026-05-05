"""Speech-to-text service using faster-whisper."""

import logging
from faster_whisper import WhisperModel

from app.config import settings

logger = logging.getLogger(__name__)


class STTService:
    """Wrapper around faster-whisper for audio transcription."""

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ):
        """Initialize the Whisper model.

        Args:
            model_size: Whisper model size (tiny, base, small, medium, large).
                       Defaults to settings.whisper_model_size.
            device: Device to run on (cpu, cuda). Defaults to settings.whisper_device.
            compute_type: Computation type (int8, float16, etc).
                         Defaults to settings.whisper_compute_type.
        """
        self.model_size = model_size or settings.whisper_model_size
        self.device = device or settings.whisper_device
        self.compute_type = compute_type or settings.whisper_compute_type

        logger.info(
            f"Loading Whisper model: size={self.model_size}, "
            f"device={self.device}, compute_type={self.compute_type}"
        )
        self.model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )
        logger.info("Whisper model loaded successfully")

    def transcribe(self, audio_path: str) -> str:
        """Transcribe an audio file to text.

        Args:
            audio_path: Path to the audio file (WAV, MP3, etc.).

        Returns:
            Transcribed text as a single string.
        """
        logger.info(f"Transcribing audio file: {audio_path}")
        segments, _ = self.model.transcribe(audio_path)
        text = " ".join(seg.text for seg in segments).strip()
        logger.info(f"Transcription complete: {len(text)} characters")
        return text
