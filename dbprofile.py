"""Database profiles: what the agent should know about a database beyond its raw schema.

A profile is a small TOML file in profiles/ - title, a line about the business, business notes
(currency, units, how values are coded), example questions, and optionally a default database URL.
Pick one with DB_PROFILE=<name> in .env. DB_PROFILE=generic works on any database with no file at all.
"""
import os
from functools import lru_cache
from pathlib import Path

try:
    import tomllib                     # Python 3.11+
except ModuleNotFoundError:            # Python 3.10
    import tomli as tomllib

PROFILE_DIR = Path(__file__).parent / "profiles"

DEFAULTS = {
    "name": "generic",
    "title": "SQL Agent",
    "description": "Chat with the database in Hebrew or English. The agent writes the SQL, checks its own "
                   "result, and shows the query behind every answer.",
    "business": "a business",
    "notes": "",
    "examples": [],
    "database_url": "",
    "answer_key": False,       # True only where eval_questions.py has the answer key for this database
}


def profile_name() -> str:
    return os.environ.get("DB_PROFILE", "").strip() or "porsche"


def available() -> list:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.toml"))


def load_profile(name: str = None) -> dict:
    """The active profile (DB_PROFILE), or the one named. Cached per name, so switching DB_PROFILE takes effect."""
    return _load(name or profile_name())


load_profile.cache_clear = lambda: _load.cache_clear()


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    if name in ("generic", "none"):
        return dict(DEFAULTS)
    path = Path(name) if name.endswith(".toml") else PROFILE_DIR / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"No profile '{name}'. Available: {', '.join(available() + ['generic'])}. "
                                f"Set DB_PROFILE in .env, or add profiles/{name}.toml")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"{path.name}: unknown setting(s) {', '.join(sorted(unknown))}")
    return {**DEFAULTS, "name": path.stem, **data}
