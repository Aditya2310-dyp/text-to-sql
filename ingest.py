"""
ingest.py — turns an uploaded file (csv, xlsx, or an existing sqlite db)
into a SQLite file on disk, so the rest of the pipeline never needs to
know or care what format the user originally uploaded.
"""

import os
import io
import sqlite3
import pandas as pd

UPLOAD_DIR = "uploaded_dbs"


def _clean_table_name(name: str) -> str:
    name = os.path.splitext(name)[0]
    return "".join(c if c.isalnum() else "_" for c in name)


def ingest_file(uploaded_file, save_as: str) -> str:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db_path = os.path.join(UPLOAD_DIR, save_as)
    filename = uploaded_file.name.lower()
    raw_bytes = uploaded_file.getvalue()

    if filename.endswith(".db") or filename.endswith(".sqlite") or filename.endswith(".sqlite3"):
        with open(db_path, "wb") as f:
            f.write(raw_bytes)
        return db_path

    con = sqlite3.connect(db_path)

    if filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(raw_bytes))
        table_name = _clean_table_name(uploaded_file.name)
        df.to_sql(table_name, con, if_exists="replace", index=False)

    elif filename.endswith(".xlsx") or filename.endswith(".xls"):
        sheets = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=None)
        for sheet_name, df in sheets.items():
            table_name = _clean_table_name(sheet_name)
            df.to_sql(table_name, con, if_exists="replace", index=False)

    else:
        con.close()
        raise ValueError(f"Unsupported file type: {uploaded_file.name}")

    con.close()
    return db_path

if __name__ == "__main__":
    import pandas as pd

    # create a tiny test CSV to prove the pipeline works
    df = pd.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]})
    df.to_csv("test_people.csv", index=False)

    class FakeUpload:
        """mimics Streamlit's UploadedFile just enough for our function to work"""
        def __init__(self, path):
            self.name = path
            with open(path, "rb") as f:
                self._bytes = f.read()
        def getvalue(self):
            return self._bytes
        def read(self):
            return self._bytes

    fake = FakeUpload("test_people.csv")
    result_path = ingest_file(fake, "test_output.sqlite")
    print("Created:", result_path)

    import sqlite3
    con = sqlite3.connect(result_path)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print("Tables:", cur.fetchall())
    cur.execute("SELECT * FROM test_people")
    print("Rows:", cur.fetchall())