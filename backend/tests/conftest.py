import os
import tempfile

# Must run before `app` is imported so tests never touch the real learned procedure in backend/data.
os.environ["TEACHBACK_DATA_DIR"] = tempfile.mkdtemp(prefix="teachback-test-")
# Tests never reach a real database, even if a developer has Tiger credentials in .env
# (an existing variable, even empty, is not overridden by python-dotenv).
os.environ["TIGER_DATABASE_URL"] = ""
# Likewise tests never call the real Gemini or ElevenLabs APIs; tests that need a key set a fake one explicitly.
os.environ["GEMINI_API_KEY"] = ""
os.environ["ELEVENLABS_API_KEY"] = ""
