from __future__ import annotations

import ast
from pathlib import Path


def test_python_main_starts_uvicorn() -> None:
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "run"
        for node in tree.body
    )
    assert 'os.getenv("TRADELATIN_EMULATOR_HOST", "127.0.0.1")' in source
    assert 'os.getenv("TRADELATIN_EMULATOR_PORT", "8000")' in source
    assert 'if __name__ == "__main__":' in source
    assert "uvicorn.run(app, host=host, port=port" in source
