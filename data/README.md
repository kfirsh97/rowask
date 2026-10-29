# Sample databases (SQLite)

| File | What | Source |
|---|---|---|
| `porsche.sqlite` | A SQLite copy of this project's Porsche sales database – 18 tables, 58,339 rows | Built from `../sql` by `python setup/build_sqlite.py` |
| `chinook.sqlite` | Chinook, a public sample database of a digital music store (artists, albums, tracks, customers, invoices) | [lerocha/chinook-database](https://github.com/lerocha/chinook-database), © 2008-2024 Luis Rocha, MIT license |

Both are opened read-only by the agent. They let anyone try it with no database server, and they back
the tests in `tests/test_any_database.py`.
