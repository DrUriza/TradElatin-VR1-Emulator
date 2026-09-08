from app.settings import settings


def is_valid_glassnode_key(x_api_key: str | None) -> bool:
    return x_api_key == settings.emulator_glassnode_api_key
