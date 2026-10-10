"""Shared-database contention and ownership regressions for material states."""
import json
import sqlite3

from workflow.material_pipeline.production_bridge import initialize, set_state


def fixture_database(tmp_path):
    database = tmp_path / 'queue.sqlite'
    initialize(database)
    owned = str(tmp_path / 'material_intake' / 'candidate' / 'manifest.json')
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE editorial_items(source_url TEXT,manifest_path TEXT,state TEXT,error TEXT)')
        connection.execute('INSERT INTO material_intakes VALUES(?,?,?,?,?,?)',
                           ('candidate', 'hash', 'packet', 'withdrawn', json.dumps({'reason': 'withdrawn'}), 1))
        connection.execute('INSERT INTO editorial_backlog(id,state) VALUES(?,?)', ('candidate', 'withdrawn'))
        connection.executemany('INSERT INTO editorial_items VALUES(?,?,?,?)',
                               [('owned', owned, 'withdrawn', None), ('other', 'outside/manifest.json', 'ready', None)])
    return database


def test_unchanged_state_does_not_acquire_a_write_lock(tmp_path):
    database = fixture_database(tmp_path)
    writer = sqlite3.connect(database)
    try:
        writer.execute('BEGIN IMMEDIATE')
        set_state(database, 'candidate', 'withdrawn', {'reason': 'withdrawn'})
        assert writer.execute('SELECT updated FROM material_intakes').fetchone()[0] == 1
    finally:
        writer.rollback()
        writer.close()


def test_state_reconciles_owned_rows_and_preserves_other_items(tmp_path):
    database = fixture_database(tmp_path)
    set_state(database, 'candidate', 'material_needs_revision', {'reason': 'gap'})
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT state FROM material_intakes').fetchone()[0] == 'material_needs_revision'
        connection.execute("UPDATE editorial_backlog SET state='stale'")
        connection.execute("UPDATE editorial_items SET error='stale' WHERE source_url='owned'")
    set_state(database, 'candidate', 'material_needs_revision', {'reason': 'gap'})
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT state FROM editorial_backlog').fetchone()[0] == 'material_needs_revision'
        assert connection.execute("SELECT state,error FROM editorial_items WHERE source_url='owned'").fetchone() == ('material_needs_revision', 'gap')
        assert connection.execute("SELECT state,error FROM editorial_items WHERE source_url='other'").fetchone() == ('ready', None)
