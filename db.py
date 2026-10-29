"""Database connection for the SQL agent (read-only user)."""
import os

import pymssql
from dotenv import load_dotenv

load_dotenv()


def connect():
    """Open a connection to the Porsche database using the settings in .env."""
    return pymssql.connect(
        server=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "1433")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "Porsche"),
        login_timeout=10,
        timeout=30,          # stop any single query after 30 seconds
        as_dict=True,
    )
