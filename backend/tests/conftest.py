import os
import tempfile

# Point the app at a throwaway config dir before any vibonomics module reads its config.
os.environ.setdefault("VIBONOMICS_CONFIG_DIR", tempfile.mkdtemp(prefix="vibonomics-test-"))
os.environ.setdefault("VIBONOMICS_SECRET_KEY", "test-secret")
