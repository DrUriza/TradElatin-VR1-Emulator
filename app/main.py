import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request

from app.settings import settings

from app.admin.routes import router as admin_router
from app.core.simulation_engine import engine
from app.providers.coinglass.routes import router as coinglass_router
from app.providers.cryptoquant.routes import router as cryptoquant_router
from app.providers.glassnode.routes import router as glassnode_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(engine.run())
    try:
        yield
    finally:
        engine.running = False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Market API Emulator",
    version="2.1.1",
    description=(
        "Emula los endpoints de CoinGlass, CryptoQuant y Glassnode "
        "consumidos por TradElatin VR1, a partir de un estado de "
        "mercado central y coherente."
    ),
    lifespan=lifespan,
)

@app.middleware("http")
async def processing_compatibility(request: Request, call_next):
    """Accept Processing Acquisition's provider-neutral localhost requests.

    Processing sends the real provider path plus X-TradELATIN-Provider. The
    Emulator keeps its native prefixed API for manual use, while this adapter
    rewrites only TradELATIN requests and injects the Emulator's fake provider
    credential. No real API key is involved.
    """
    provider = request.headers.get("X-TradELATIN-Provider", "").strip().lower()
    prefixes = {
        "coinglass": "/coinglass",
        "cryptoquant": "/cryptoquant",
        "glassnode": "/glassnode",
    }
    prefix = prefixes.get(provider)
    if prefix and not request.scope["path"].startswith(prefix + "/"):
        path = request.scope["path"]
        request.scope["path"] = prefix + path
        request.scope["raw_path"] = request.scope["path"].encode("ascii", errors="ignore")
        headers = list(request.scope.get("headers", []))
        header_names = {name.lower() for name, _ in headers}
        if provider == "coinglass" and b"cg-api-key" not in header_names:
            headers.append((b"cg-api-key", settings.emulator_coinglass_api_key.encode()))
        elif provider == "cryptoquant" and b"authorization" not in header_names:
            token = f"Bearer {settings.emulator_cryptoquant_api_key}".encode()
            headers.append((b"authorization", token))
        elif provider == "glassnode" and b"x-api-key" not in header_names:
            headers.append((b"x-api-key", settings.emulator_glassnode_api_key.encode()))
        request.scope["headers"] = headers
    return await call_next(request)


app.include_router(coinglass_router, prefix="/coinglass")
app.include_router(cryptoquant_router, prefix="/cryptoquant")
app.include_router(glassnode_router, prefix="/glassnode")
app.include_router(admin_router)
