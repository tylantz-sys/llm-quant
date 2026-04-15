import duckdb

conn = duckdb.connect("data/llm_quant.duckdb", read_only=True)
tables = [
    "broker_submitted_orders",
    "broker_fill_events",
    "broker_event_ledger",
    "broker_position_lifecycle",
]
for table in tables:
    count = conn.execute(f"select count(*) from {table}").fetchone()[0]
    print(f"{table}: {count}")
conn.close()
