"""Database profiles: what the agent should know about a database beyond its raw schema.

A profile is a small TOML file in profiles/ - title, a line about the business, business notes
(currency, units, how values are coded), example questions, and optionally a default database URL.
Pick one with DB_PROFILE=<name> in .env. DB_PROFILE=generic works on any database with no file at all.
"""
import contextvars
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
    "title": "Your database",
    "description": "Chat with the database in Hebrew or English. The agent writes the SQL, checks its own "
                   "result, and shows the query behind every answer.",
    "business": "a business",
    "notes": "",
    "examples": [],
    "database_url": "",
    "answer_key": False,       # True only where eval_questions.py has the answer key for this database
    "metrics": [],             # the metric dictionary: [[metrics]] tables, see metrics.py
    "report": {},              # optional: a BI report shown on the Dashboard tab, {title, embed_url, url} - see
                               # check_report(); for a report built elsewhere (Data Studio) on the same data
    "drill": [],               # optional drill-down levels, e.g. [["Region", "State_Province", "City"]] -
                               # without it, the levels are found in the data (drill.py)
}


# The description built for an uploaded file, for one browser session (see db.SESSION_URL).
SESSION_PROFILE = contextvars.ContextVar("session_profile", default=None)


def profile_name() -> str:
    return os.environ.get("DB_PROFILE", "").strip() or "porsche"


def available() -> list:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.toml"))


def load_profile(name: str = None) -> dict:
    """The active profile: an upload's (this session), else DB_PROFILE, or the one named.
    Cached per name, so switching DB_PROFILE takes effect."""
    if name is None and SESSION_PROFILE.get():
        return SESSION_PROFILE.get()
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
    import metrics
    try:
        metrics.parse(data.get("metrics"))
    except ValueError as e:
        raise ValueError(f"{path.name}: {e}") from None
    try:
        check_report(data.get("report") or {})
    except ValueError as e:
        raise ValueError(f"{path.name}: {e}") from None
    return {**DEFAULTS, "name": path.stem, **data}


# Hosts whose embed pages may be shown in the app: an iframe of any other address is refused.
REPORT_HOSTS = ("https://datastudio.google.com/embed/", "https://lookerstudio.google.com/embed/")


def check_report(report: dict) -> None:
    """A [report] table: title and embed_url required (a Data Studio embed link), url (the normal link) optional."""
    if not report:
        return
    unknown = set(report) - {"title", "embed_url", "url"}
    if unknown:
        raise ValueError(f"[report]: unknown setting(s) {', '.join(sorted(unknown))}")
    if not report.get("title") or not report.get("embed_url"):
        raise ValueError("[report] needs a title and an embed_url")
    if not str(report["embed_url"]).startswith(REPORT_HOSTS):
        raise ValueError("[report] embed_url must be a Data Studio embed link (https://datastudio.google.com/embed/...)")
