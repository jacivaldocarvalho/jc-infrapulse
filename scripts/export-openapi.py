"""Export the current API contract without a server, lifespan or database."""

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
application = importlib.import_module("app.main").app
print(json.dumps(application.openapi(), sort_keys=True, separators=(",", ":")))
