import sqlite3, os
db = r'datasets/olist.db'
print(f'File size: {os.path.getsize(db)/1024/1024:.1f} MB')
con = sqlite3.connect(db)
cur = con.cursor()
tables = cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f'Tables: {len(tables)}')
for (t,) in tables:
    count = cur.execute(f'SELECT COUNT(*) FROM [{t}]').fetchone()[0]
    cols = [r[1] for r in cur.execute(f'PRAGMA table_info([{t}])').fetchall()]
    print(f'\n  TABLE: {t}')
    print(f'  Rows : {count:,}')
    print(f'  Cols : {", ".join(cols)}')
con.close()
