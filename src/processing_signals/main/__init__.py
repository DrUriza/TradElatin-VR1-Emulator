"""TradELATIN runtime package.

Keep package initialization intentionally side-effect free. Runtime entrypoints
must be imported from their concrete modules (for example
``processing_signals.main.runtime_orchestrator``) so unrelated verticals cannot
break ``python main.py`` during package import.
"""
