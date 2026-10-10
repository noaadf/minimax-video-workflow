import sqlite3
import threading

from workflow.material_pipeline.production_bridge import _queue


def test_editorial_compiler_waits_for_existing_writer_without_dispatch(tmp_path):
    database = tmp_path/'queue.sqlite'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE editorial_items(source_url TEXT PRIMARY KEY, state TEXT)')
    acquired = threading.Event()

    def existing_writer():
        with sqlite3.connect(database) as c:
            c.execute('BEGIN IMMEDIATE')
            acquired.set()
            threading.Event().wait(.15)

    writer = threading.Thread(target=existing_writer)
    writer.start()
    assert acquired.wait(2)
    queue = _queue(database)
    assert not hasattr(queue, 'submit')
    with queue.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO editorial_items VALUES(?,?)', ('source', 'planning'))
    writer.join(2)
    with sqlite3.connect(database) as c:
        assert c.execute('SELECT state FROM editorial_items').fetchone()[0] == 'planning'
        assert c.execute("SELECT name FROM sqlite_master WHERE name IN ('jobs','tasks')").fetchall() == []


def test_compiler_terminal_update_retries_before_implicit_write(tmp_path):
    database = tmp_path/'queue.sqlite'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE editorial_items(source_url TEXT PRIMARY KEY, state TEXT)')
        c.execute('INSERT INTO editorial_items VALUES(?,?)', ('source', 'planning'))
    acquired = threading.Event()

    def existing_writer():
        with sqlite3.connect(database) as c:
            c.execute('BEGIN IMMEDIATE')
            acquired.set()
            threading.Event().wait(2.3)

    writer = threading.Thread(target=existing_writer)
    writer.start()
    assert acquired.wait(2)
    try:
        with _queue(database).connect() as c:
            c.execute('PRAGMA busy_timeout=1')
            # batch.produce does not issue BEGIN for this terminal write.
            c.execute('UPDATE editorial_items SET state=? WHERE source_url=?',
                      ('awaiting_storyboard', 'source'))
    finally:
        writer.join(4)
    assert not writer.is_alive()
    with sqlite3.connect(database) as c:
        assert c.execute('SELECT state FROM editorial_items').fetchone()[0] == 'awaiting_storyboard'
        assert c.execute("SELECT name FROM sqlite_master WHERE name IN ('jobs','tasks')").fetchall() == []
