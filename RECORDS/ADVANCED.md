# Requirements Manager — Advanced Usage

These functions are not exposed as MCP tools but are available for CLI debugging and testing.
Invoke them by importing and calling directly from a Python script or REPL.

## get_record(id: int) -> dict

Fetch a single record by integer ID. Returns all fields including timestamps.

```python
from tools.records import get_record
record = await get_record(id=1, ctx=ctx)
```

## set_threshold(threshold: float) -> str

Update the active cosine similarity threshold at runtime without restarting the server.
Valid range: 0.0–1.0. Default: 0.50.

```python
from tools.records import set_threshold
await set_threshold(threshold=0.65, ctx=ctx)
```

## search_records (full options)

The MCP tool always uses threshold=0.50, limit=10, no scores.
For custom searches, call the engine directly:

```python
results = await state.engine.async_search(query, threshold=0.70, limit=20)
```

## CLI startup

```bash
python3 src/server.py --records-dir RECORDS --scripts-dir SCRIPTS --threshold 0.50 --ce-threshold -10.0
```
