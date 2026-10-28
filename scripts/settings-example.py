"""OpenAI-compatible endpoint settings for ``summary_generator.py``.

Copy this file to ``settings.py`` in the same directory and fill in real
values. ``settings.py`` is gitignored.
"""

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_API_KEY = "replace-with-your-api-key"
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_TIMEOUT_SECONDS = 60
OPENAI_MAX_RETRIES = 2
OPENAI_TEMPERATURE = 0.2
