"""Run the companion: python -m companion."""

from companion.store import open_store, store_path


def main():
    path = store_path()
    with open_store(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
    print(f"Leetotter companion: store {path}, schema version {version}")


if __name__ == "__main__":
    main()
