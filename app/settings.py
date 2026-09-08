from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_yaml_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}

    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class Settings(BaseSettings):
    """
    Llaves ficticias del emulador (nunca llaves reales).
    Se leen de variables de entorno / .env; ver .env.example.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    emulator_coinglass_api_key: str = "emulator-coinglass-key"
    emulator_cryptoquant_api_key: str = "emulator-cryptoquant-key"
    emulator_glassnode_api_key: str = "emulator-glassnode-key"


settings = Settings()
yaml_config = _load_yaml_config()

simulation_config = yaml_config.get("simulation", {})
SIMULATION_SEED: int = simulation_config.get("seed", 4271)
SIMULATION_UPDATE_SECONDS: int = simulation_config.get("update_seconds", 10)
SIMULATION_HISTORY_SIZE: int = simulation_config.get("history_size", 500)
EMULATOR_RECORD_COUNT: int = int(simulation_config.get("endpoint_records", 500))
LIQUIDATIONS_HISTORY_SAMPLES_PER_PERIOD: int = max(2, int(simulation_config.get("liquidations_history_samples_per_period", 4)))
SIMULATION_INITIAL_REGIME: str = simulation_config.get("initial_regime", "lateral")
