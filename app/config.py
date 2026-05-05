from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./voice_crud.db"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e4b"
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    confirmation_ttl_seconds: int = 300
    session_ttl_seconds: int = 1800  # 30 minutes


settings = Settings()
