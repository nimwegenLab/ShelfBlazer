"""Shared by the core tests (not a test module)."""
import os


def purge(store, rid, actor):
    """Stand-in for the planned purge command: the record file is gone from the repository and the store.
    The app itself never deletes records (archive only); tests use this when they need a record that no longer exists."""
    path = store.path_of(rid, store.records[rid]["type"])
    os.remove(path)
    store.commit([path], f"{rid}: purge", actor)
    del store.records[rid]
    store._notify(rid)
