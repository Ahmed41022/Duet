"""Defaults and explicit TOML overrides for Duet settings."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Config:
    received_directory: Path = field(
        default_factory=lambda: Path.home() / "Duet" / "Received"
    )
    idle_timeout_seconds: int = 900
    log_level: str = "INFO"

    max_file_size: int | None = None
    max_parallel_uploads: int = 3

    def __post_init__(self) -> None:
        if type(self.idle_timeout_seconds) is not int or self.idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be a positive integer")
        if self.max_file_size is not None and (
            type(self.max_file_size) is not int or self.max_file_size < 0
        ):
            raise ValueError("max_file_size must be a non-negative integer")
        if type(self.max_parallel_uploads) is not int or self.max_parallel_uploads < 1:
            raise ValueError("max_parallel_uploads must be a positive integer")
        if self.log_level not in LOG_LEVELS:
            valid_levels = ", ".join(sorted(LOG_LEVELS))
            raise ValueError(f"log_level must be one of {valid_levels}")


def load_config(path: Path | None = None) -> Config:
    """Load an explicitly selected TOML file, or use defaults without reading disk."""
    defaults = Config()
    if path is None:
        return defaults

    with path.open("rb") as config_file:
        values = tomllib.load(config_file)

    unknown_keys = values.keys() - {
        "received_directory",
        "idle_timeout_seconds",
        "log_level",
        "max_file_size",
        "max_parallel_uploads",
    }
    if unknown_keys:
        raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown_keys))}")

    received = defaults.received_directory
    if "received_directory" in values:
        directory = values["received_directory"]
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError("received_directory must be a non-empty path string")
        received = path.resolve().parent / Path(directory).expanduser()

    log_level = values.get("log_level", defaults.log_level)
    if not isinstance(log_level, str):
        raise ValueError("log_level must be a string")

    idle_timeout_seconds = values.get(
        "idle_timeout_seconds", defaults.idle_timeout_seconds
    )
    return Config(
        received_directory=received,
        idle_timeout_seconds=idle_timeout_seconds,
        log_level=log_level.upper(),
        max_file_size=values.get("max_file_size"),
        max_parallel_uploads=values.get(
            "max_parallel_uploads", defaults.max_parallel_uploads
        ),
    )
