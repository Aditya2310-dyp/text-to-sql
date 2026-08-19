import sqlite3
from itertools import combinations


def _quote_identifier(name: str) -> str:
    """Safely quote a SQLite table/column identifier."""
    return '"' + name.replace('"', '""') + '"'


def _get_tables_and_columns(db_path: str) -> dict:
    """
    Returns:
    {
        "customers": [
            {"name": "customer_id", "type": "INTEGER"},
            {"name": "name", "type": "TEXT"}
        ]
    }
    """
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name NOT LIKE 'sqlite_%'
    """)

    tables = [row[0] for row in cur.fetchall()]

    result = {}

    for table in tables:
        cur.execute(
            f"PRAGMA table_info({_quote_identifier(table)})"
        )

        result[table] = [
            {
                "name": row[1],
                "type": row[2].upper()
            }
            for row in cur.fetchall()
        ]

    con.close()

    return result


def _normalise_name(name: str) -> str:
    """
    Normalises column/table names for comparison.

    customer_id -> customerid
    CustomerID  -> customerid
    """
    return (
        name.lower()
        .replace("_", "")
        .replace("-", "")
        .strip()
    )


def _looks_like_key_pair(
    from_column: str,
    to_table: str,
    to_column: str
) -> bool:
    """
    Determines whether two columns look like a foreign-key relationship.
    """

    from_col = _normalise_name(from_column)
    to_col = _normalise_name(to_column)
    table = _normalise_name(to_table)

    # Exact same key name:
    # orders.customer_id -> customers.customer_id
    if from_col == to_col and (
        "id" in from_col or
        "key" in from_col
    ):
        return True

    # customer_id -> customers.customer_id
    table_without_plural = table[:-1] if table.endswith("s") else table

    possible_names = {
        f"{table}_id",
        f"{table_without_plural}_id",
        f"{table}id",
        f"{table_without_plural}id",
    }

    possible_names = {
        _normalise_name(name)
        for name in possible_names
    }

    return from_col in possible_names


def _is_numeric_type(sql_type: str) -> bool:
    sql_type = sql_type.upper()

    return any(
        keyword in sql_type
        for keyword in [
            "INT",
            "REAL",
            "NUM",
            "DEC",
            "FLOAT",
            "DOUBLE"
        ]
    )


def _compatible_types(type_a: str, type_b: str) -> bool:
    """
    Basic SQLite type compatibility check.
    """

    a = type_a.upper()
    b = type_b.upper()

    if a == b:
        return True

    if _is_numeric_type(a) and _is_numeric_type(b):
        return True

    return False


def _is_unique(
    db_path: str,
    table: str,
    column: str
) -> bool:

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    t = _quote_identifier(table)
    c = _quote_identifier(column)

    cur.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(DISTINCT {c})
        FROM {t}
        WHERE {c} IS NOT NULL
        """
    )

    total, distinct = cur.fetchone()

    con.close()

    if total == 0:
        return False

    return total == distinct


def _value_overlap_ratio(
    db_path: str,
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str
) -> float:

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    ft = _quote_identifier(from_table)
    fc = _quote_identifier(from_column)
    tt = _quote_identifier(to_table)
    tc = _quote_identifier(to_column)

    cur.execute(
        f"""
        SELECT
            COUNT(DISTINCT a.{fc}),
            COUNT(DISTINCT CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM {tt} b
                    WHERE b.{tc} = a.{fc}
                )
                THEN a.{fc}
            END)
        FROM {ft} a
        WHERE a.{fc} IS NOT NULL
        """
    )

    total, matched = cur.fetchone()

    con.close()

    if not total:
        return 0.0

    return matched / total


def infer_relationships(
    db_path: str,
    overlap_threshold: float = 0.8
) -> list[dict]:

    tables = _get_tables_and_columns(db_path)

    found = []

    table_names = list(tables.keys())

    # Compare every pair of tables only once
    for table_a, table_b in combinations(table_names, 2):

        columns_a = tables[table_a]
        columns_b = tables[table_b]

        for col_a in columns_a:

            for col_b in columns_b:

                name_match_ab = _looks_like_key_pair(
                    col_a["name"],
                    table_b,
                    col_b["name"]
                )

                name_match_ba = _looks_like_key_pair(
                    col_b["name"],
                    table_a,
                    col_a["name"]
                )

                if not name_match_ab and not name_match_ba:
                    continue

                # Data types should be compatible
                if not _compatible_types(
                    col_a["type"],
                    col_b["type"]
                ):
                    continue

                unique_a = _is_unique(
                    db_path,
                    table_a,
                    col_a["name"]
                )

                unique_b = _is_unique(
                    db_path,
                    table_b,
                    col_b["name"]
                )

                if name_match_ab and unique_b and not unique_a:

                    from_table = table_a
                    from_column = col_a

                    to_table = table_b
                    to_column = col_b

                elif name_match_ba and unique_a and not unique_b:

                    from_table = table_b
                    from_column = col_b

                    to_table = table_a
                    to_column = col_a

                else :
                    continue

                overlap = _value_overlap_ratio(
                    db_path,
                    from_table,
                    from_column["name"],
                    to_table,
                    to_column["name"]
                )

                if overlap >= overlap_threshold:

                    found.append({
                        "from_table": from_table,
                        "from_column": from_column["name"],
                        "to_table": to_table,
                        "to_column": to_column["name"],
                        "overlap": round(overlap, 3),
                        "confidence": "high"
                    })

    return found


if __name__ == "__main__":

    relationships = infer_relationships(
        "chinookdb.sqlite"
    )

    print("\nDetected relationships:\n")

    for relationship in relationships:
        print(
            f"{relationship['from_table']}."
            f"{relationship['from_column']}"
            f" -> "
            f"{relationship['to_table']}."
            f"{relationship['to_column']}"
            f" | overlap={relationship['overlap']}"
            f" | confidence={relationship['confidence']}"
        )