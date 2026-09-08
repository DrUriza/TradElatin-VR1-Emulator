from fastapi import APIRouter, HTTPException

from app.core.simulation_engine import engine
from app.endpoint_registry import registered_endpoints
from app.settings import EMULATOR_RECORD_COUNT

router = APIRouter(tags=["Administration"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "endpoint_records": EMULATOR_RECORD_COUNT,
        "simulation_model": "gbm_black_scholes_stochastic_vol_jump_diffusion_markov",
    }


@router.get("/admin/state")
def state() -> dict:
    return engine.get_state().to_dict()


@router.post("/admin/reset")
def reset() -> dict:
    state = engine.reset()
    # Import lazily to avoid a module cycle during application startup.
    from app.providers.coinglass.routes import clear_runtime_caches

    clear_runtime_caches()
    return state.to_dict()


@router.post("/admin/advance")
def advance() -> dict:
    return engine.advance().to_dict()


@router.post("/admin/regime/{regime}")
def change_regime(regime: str) -> dict:
    try:
        engine.set_regime(regime)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "success": True,
        "regime": regime,
    }


@router.post("/admin/event/liquidation")
def trigger_liquidation_event() -> dict:
    new_state = engine.trigger_liquidation_event()
    return {
        "success": True,
        "state": new_state.to_dict(),
    }


@router.get("/admin/endpoints")
def endpoints() -> dict:
    items = registered_endpoints()
    return {
        "count": len(items),
        "endpoint_records": EMULATOR_RECORD_COUNT,
        "simulation_model": "gbm_black_scholes_stochastic_vol_jump_diffusion_markov",
        "endpoints": items,
    }
