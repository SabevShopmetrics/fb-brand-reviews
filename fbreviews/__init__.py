"""Facebook brand → location pages → reviews + replies, driven by a browser-use agent on our own model."""

import os

# Before browser_use is imported anywhere: no telemetry, no update check.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_NO_UPDATE_CHECK", "1")

__version__ = "0.1.0"
