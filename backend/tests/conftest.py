import os
import tempfile

# Must run before `app` is imported so tests never touch the real learned procedure in backend/data.
os.environ["TEACHBACK_DATA_DIR"] = tempfile.mkdtemp(prefix="teachback-test-")
