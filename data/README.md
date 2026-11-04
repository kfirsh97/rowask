# Sample databases (SQLite)

| File | What | Source |
|---|---|---|
| `porsche.sqlite` | A SQLite copy of the Porsche sales database – 18 tables, 58,339 rows | Built from `porsche_sql/` by `python setup/build_sqlite.py` |
| `porsche_sql/` | The Porsche database as SQL Server scripts (schema, then data) – for the SQL Server setup and the upload tests | [kfirsh97/porsche-sales-database](https://github.com/kfirsh97/porsche-sales-database), the SQL project this tool was first built on |
| `steam.sqlite` | A snapshot of the Steam games warehouse – players, prices and sales for Steam's 100 most played games | Exported from BigQuery by `export_sqlite.py` in [kfirsh97/steam-pipeline](https://github.com/kfirsh97/steam-pipeline) |
| `chinook.sqlite` | Chinook, a public sample database of a digital music store (artists, albums, tracks, customers, invoices) | [lerocha/chinook-database](https://github.com/lerocha/chinook-database), © 2008-2024 Luis Rocha, MIT license |

All are opened read-only by the agent. They let anyone try it with no database server, and they back
the tests in `tests/test_any_database.py`.
