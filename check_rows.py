import duckdb
con = duckdb.connect()
result = con.execute("SELECT COUNT(*) FROM read_parquet('C:/Users/admin/Desktop/Modulario/LEAD_INDEX/raw_contacts.parquet')").fetchone()
print(f"Rows in raw_contacts.parquet: {result[0]:,}")
