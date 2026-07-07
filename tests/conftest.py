"""
Shared test helper: `lambda/` isn't an importable Python package (`lambda` is a
reserved keyword), so each handler module is loaded directly from its file path
under a unique module name to avoid sys.modules collisions between test files.
"""

import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent


def load_handler(function_name: str):
    path = REPO_ROOT / "lambda" / function_name / "handler.py"
    spec = importlib.util.spec_from_file_location(f"{function_name}_handler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
