from app.settings import settings


def is_valid_coinglass_key(cg_api_key: str | None) -> bool:
    return cg_api_key == settings.emulator_coinglass_api_key
