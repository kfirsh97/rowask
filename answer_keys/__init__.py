"""Answer keys: questions with known answers, one module per profile (answer_keys/<profile>.py).

Each module has DIALECT (the SQL the expected queries are written in) and QUESTIONS:
[{"id", "question", "expected_sql", optional "trap"}]. eval.py scores the agent against the key of the
active profile. To measure the agent on your own database, copy superstore.py to <your profile>.py.
"""
import importlib


def load(profile: str):
    """(dialect, questions) for this profile, or None when it has no answer key."""
    try:
        mod = importlib.import_module(f"answer_keys.{profile}")
    except ModuleNotFoundError:
        return None
    return mod.DIALECT, mod.QUESTIONS
