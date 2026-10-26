"""Bounded synthetic benchmark; no app server, credentials or external calls."""
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import teaching
import teaching_assets

def measure():
    db = sqlite3.connect(':memory:')
    teaching.initialize(db)
    if '--without-indexes' in sys.argv:
        db.execute('DROP INDEX teaching_sources_request')
        db.execute('DROP INDEX teaching_asset_refs_file')
    for i in range(1000):
        db.execute('INSERT INTO teaching_sources VALUES(?,?,?,?,?,?)', ('u','a',f'i{i}',f'd{i}',f'r{i}','{}'))
        db.execute('INSERT INTO teaching_asset_files VALUES(?,?,?,?,?,?,?)', ('u','a',f'h{i}','application/pdf',b'synthetic',None,None))
        db.execute('INSERT INTO teaching_asset_refs VALUES(?,?,?,?,?,?)', ('u','a',f'r{i}','file:0',f'h{i}','application/pdf'))
    db.commit()
    start = time.perf_counter()
    for _ in range(70):
        teaching_assets.prune(db, 'u')
    elapsed = time.perf_counter() - start
    assert db.execute('SELECT count(*) FROM teaching_asset_files').fetchone()[0] == 1000
    assert db.execute('SELECT count(*) FROM teaching_asset_refs').fetchone()[0] == 1000
    db.close()
    return elapsed

if __name__ == '__main__':
    values = [measure() for _ in range(3)]
    print(json.dumps({'seconds':values,'median_seconds':statistics.median(values)}))
