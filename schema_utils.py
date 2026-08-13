import sqlite3

def get_schema_text(db_path: str) -> str:
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    #1. get all table names
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cur.fetchall()]

    lines = []
    for table in tables:
        #2. get columns for this table
        cur.execute(f'PRAGMA table_info("{table}")')
        cols = cur.fetchall()
        col_desc = ", ".join(f"{c[1]} ({c[2]})" for c in cols)

        # 3. get foreign keys for this table
        cur.execute(f'PRAGMA foreign_key_list("{table}")')
        fks = cur.fetchall()
        fk_desc = ""
        if fks:
            fk_parts = [f"{fk[3]} -> {fk[2]}.{fk[4]}" for fk in fks]
            fk_desc = "\n  Foreign keys: " + ", ".join(fk_parts)

        lines.append(f"Table {table}: {col_desc}{fk_desc}")

    con.close()
    return "\n".join(lines)

if __name__ == "__main__":
    print(get_schema_text("chinookdb.sqlite"))