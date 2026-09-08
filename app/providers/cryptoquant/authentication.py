from app.settings import settings


def is_valid_cryptoquant_token(authorization: str | None) -> bool:
    expected = f"Bearer {settings.emulator_cryptoquant_api_key}"
    return authorization == expected
