"""Import old devis rows from a JSON export into the facturation database.

Usage:
    python import_devis.py devis_rows.json [path/to/facturation.db]

- Inserts into the `devis` table, skipping any devis_num that already exists.
- Repairs mojibake (UTF-8 text that was mis-decoded as Latin-1, e.g. "ChÃ¨que" -> "Chèque").
- Keeps the original ids so existing document numbering stays consistent.
"""
import json
import os
import sqlite3
import sys

COLUMNS = [
    "id", "devis_num", "devis_date", "client_code", "client_raison",
    "client_nom", "client_adresse", "client_rc", "client_nif", "client_nis",
    "client_ai", "client_email", "client_tel", "lignes", "total_ht",
    "remise_pct", "remise_montant", "montant_tva", "total_ttc", "apply_tva",
    "objet", "reglement", "paiement", "validite_jours", "delai_min",
    "delai_max", "created_by", "created_at",
]


def fix_mojibake(value):
    if not isinstance(value, str):
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired


def normalize_created_at(value):
    # "2026-03-01 13:13:43.529933+00" -> "2026-03-01 13:13:43"
    if not isinstance(value, str):
        return value
    return value.split("+")[0].split(".")[0]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    json_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "facturation.db")

    if not os.path.exists(db_path):
        print(f"ERROR: database not found: {db_path}")
        print("Start the app once first so it creates the database and tables.")
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        rows = json.load(f)

    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" for _ in COLUMNS)
    sql = (f"INSERT INTO devis ({','.join(COLUMNS)}) VALUES ({placeholders}) "
           f"ON CONFLICT(devis_num) DO NOTHING")

    inserted = skipped = 0
    for row in sorted(rows, key=lambda r: r.get("id") or 0):
        values = []
        for col in COLUMNS:
            v = row.get(col)
            if col == "created_at":
                v = normalize_created_at(v)
            elif col not in ("lignes",):
                v = fix_mojibake(v)
            values.append(v)
        # avoid id collisions with rows already in the table
        existing = conn.execute("SELECT 1 FROM devis WHERE id = ? AND devis_num != ?",
                                (row.get("id"), row.get("devis_num"))).fetchone()
        if existing:
            values[0] = None  # let SQLite pick a new id
        cur = conn.execute(sql, values)
        if cur.rowcount:
            inserted += 1
        else:
            skipped += 1
            print(f"  skipped (already exists): {row.get('devis_num')}")

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM devis").fetchone()[0]
    conn.close()
    print(f"Done. Inserted {inserted}, skipped {skipped}. Table now has {total} devis.")


if __name__ == "__main__":
    main()
