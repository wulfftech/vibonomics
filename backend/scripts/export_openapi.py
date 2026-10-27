"""Print the API's OpenAPI document as JSON (used by the frontend's ``npm run gen:api``).

Builds the app without starting it, against a throwaway config directory so nothing is read from
or written to a real install.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    os.environ["VIBONOMICS_CONFIG_DIR"] = tempfile.mkdtemp(prefix="vibonomics-openapi-")
    # The schema lives on the inner app; a URL base would wrap it in one without docs.
    os.environ.pop("VIBONOMICS_URL_BASE", None)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from vibonomics.app import create_app

    sys.stdout.write(json.dumps(create_app().openapi(), indent=2, sort_keys=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
