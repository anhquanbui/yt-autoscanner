# Mongo → Parquet Export Guide (`tools/mongo_to_parquet.py`)

This guide explains how to use the `mongo_to_parquet.py` tool to export **MongoDB collections** into **Parquet** files for machine learning and analytics.

---

## 1. Overview

The tool:

- Connects to **MongoDB** using `MONGO_URI` from `.env` or `--uri`.
- Lets you **choose database + collection** interactively (or via env/CLI).
- Streams documents in **chunks** to avoid memory overflow.
- Normalizes MongoDB/BSON types to Parquet-friendly types.
- Writes a single **Parquet file** to the unified export directory:
  - Controlled by `EXPORT_DIR` / `OUTPUT_DIR` in `.env`
  - Defaults to: `<project_root>/data_export/`

Typical flow:

```text
MongoDB → tools/mongo_to_parquet.py → Parquet file → ML (XGBoost / sklearn / etc.)
```

---

## 2. Prerequisites

### 2.1 Environment

From project root (`yt-autoscanner/`):

```powershell
.env\Scripts\Activate.ps1   # Windows
# hoặc
source venv/bin/activate      # Linux/macOS
```

### 2.2 Install dependencies

Using project-wide dev requirements:

```powershell
pip install -r requirements-dev.txt
```

Or only for `tools`:

```powershell
pip install -r tools/requirements.txt
```

Key packages used by this tool:

- `pymongo` — MongoDB driver
- `python-dotenv` — loads `.env`
- `pandas`, `numpy` — data handling
- `pyarrow`, `fastparquet` — Parquet engines

---

## 3. Environment Variables

### 3.1 MongoDB connection

In `.env`:

```env
MONGO_URI=mongodb://localhost:27017/ytscan
MONGO_DB=ytscan
MONGO_COLLECTION=videos
```

- `MONGO_URI` – connection string (can include username/password if needed).
- `MONGO_DB` – default database (optional).
- `MONGO_COLLECTION` – default collection (optional).

If `MONGO_DB` or `MONGO_COLLECTION` is missing, the tool will show an interactive menu.

### 3.2 Export directory

Centralized via `config/path_utils.py`:

```env
EXPORT_DIR=D:\PYTHON\PROJECT\yt-autoscanner\data_export
# or (to put it next to the project root)
# EXPORT_DIR=D:\PYTHON\PROJECT\data_export
```

If `EXPORT_DIR` is not set:

- Fallback: `<project_root>/data_export/`

All Parquet files from this tool will end up there (unless overridden with `--out`).

---

## 4. How to Run

**Always run from project root** (`yt-autoscanner/`) using module mode:

```powershell
python -m tools.mongo_to_parquet
```

This ensures Python can correctly import:

```python
from config.path_utils import get_export_dir
```

---

## 5. Command-line Arguments

Usage (simplified):

```text
python -m tools.mongo_to_parquet [options]
```

Supported options:

| Option        | Type    | Default                         | Description |
|---------------|---------|---------------------------------|-------------|
| `--uri`       | string  | `MONGO_URI` or `mongodb://localhost:27017` | MongoDB connection URI |
| `--db`        | string  | `MONGO_DB` or interactive menu | Database name |
| `--collection`, `--coll` | string | `MONGO_COLLECTION` or menu | Collection name |
| `--query`     | string  | `{}` (no filter)                | MongoDB filter as JSON string |
| `--limit`     | int     | None (no limit)                 | Max number of documents |
| `--chunk`     | int     | `100000`                        | Docs per write chunk |
| `--out`       | string  | `mongo_export.parquet`          | Output file name (relative to export dir) |

---

## 6. Typical Usage Patterns

### 6.1 Basic export (no filter)

Export entire collection:

```powershell
python -m tools.mongo_to_parquet
```

Flow:

1. Prompts you to choose DB (if `MONGO_DB` not set).
2. Prompts you to choose collection (if `MONGO_COLLECTION` not set).
3. Exports **all documents** in chunks to:

   ```text
   <EXPORT_DIR>/mongo_export.parquet
   ```

---

### 6.2 Export only completed videos (for ML)

Example filter on `processed_status = "complete"`:

```powershell
python -m tools.mongo_to_parquet `
  --collection processed_videos `
  --query "{\"processed_status\": \"complete\"}" `
  --out processed_complete.parquet
```

- On Windows PowerShell, escape quotes like above.
- On Linux/macOS:

  ```bash
  python -m tools.mongo_to_parquet     --collection processed_videos     --query '{"processed_status": "complete"}'     --out processed_complete.parquet
  ```

Result:

```text
<EXPORT_DIR>/processed_complete.parquet
```

---

### 6.3 Limit number of documents

When you just want a sample (e.g. 200k docs):

```powershell
python -m tools.mongo_to_parquet `
  --limit 200000 `
  --out sample_200k.parquet
```

---

### 6.4 Adjust chunk size

If you have:

- **Less RAM** → use smaller chunk (e.g. 50k)
- **More RAM** → bigger chunk (e.g. 200k) for speed

Example:

```powershell
python -m tools.mongo_to_parquet `
  --chunk 50000 `
  --out mongo_export_50k_chunks.parquet
```

---

### 6.5 Custom output path inside export dir

If you want subfolders:

```powershell
python -m tools.mongo_to_parquet `
  --out videos/raw_videos.parquet
```

If `EXPORT_DIR` is `D:\PYTHON\PROJECT\data_export`, final path:

```text
D:\PYTHON\PROJECT\data_export\videos\raw_videos.parquet
```

The tool will create subfolders automatically.

---

## 7. What the Tool Does Internally

### 7.1 Normalization

Function `normalize_document()`:

- Converts BSON types:
  - `ObjectId` → `str`
  - `Decimal128` → `float` (or `str` fallback)
  - `Timestamp`, `Binary`, nested dicts, lists → safe types
- Handles edge cases like:
  - `tracking.next_poll_after` may be `None` for many docs.  
    We normalize such fields to **strings** (e.g. empty string `""`) to keep Parquet schema stable across chunks.

### 7.2 Chunked writing

Pseudocode:

```python
writer = None

for each chunk:
    df = pandas.DataFrame(chunk)
    table = pa.Table.from_pandas(df)

    if writer is None:
        writer = pq.ParquetWriter(out_path, table.schema)
    else:
        # cast schema to match first chunk
        table = table.cast(writer.schema)

    writer.write_table(table)
```

This ensures:

- Same schema for all chunks.
- No `ValueError: Table schema does not match schema used to create file`.

---

## 8. Troubleshooting

### 8.1 Warning: `no_cursor_timeout`

You may see:

```text
UserWarning: use an explicit session with no_cursor_timeout=True ...
```

This is just a **warning** from PyMongo about long-running cursors.

- For most local runs, you can safely ignore it.
- If exporting from a huge collection on a remote server, you might consider explicitly managing sessions.

### 8.2 `ValueError: Table schema does not match schema used to create file`

This typically happens when:

- A field has different inferred types in different chunks:
  - Example: `tracking.next_poll_after` is all `null` in the first chunk (schema `null`), but becomes `string` in later chunks.

Fix in this project:

- We normalize such fields (e.g. `next_poll_after`) to **string** early in `normalize_document()`.
- Then we also cast every subsequent chunk to the schema of the first chunk using:

  ```python
  table = table.cast(writer.schema)
  ```

If you add new complex fields, try to:

- Normalize them consistently (e.g. always `string` or always `int64`).
- Avoid adding “sometimes list, sometimes scalar” fields.

---

## 9. Integration with ML Workflow

Once you have `*.parquet` export, typical ML workflow:

1. Ingest + track videos via `discover_once.py` and `track_once.py`.
2. Maintain dataset using tools:
   - `archive_completed_videos.py`
   - `prune_unavailable_once.py`
3. Aggregate & feature-engineer using:
   - `worker/process_data.py`
4. Export final dataset using:
   - `tools/mongo_to_parquet.py` (e.g. from `processed_videos`).
5. In your ML notebook / script:

   ```python
   import pandas as pd

   df = pd.read_parquet("data_export/processed_complete.parquet")
   # Train with XGBoost / sklearn...
   ```

---

## 10. Summary

- **Run from project root**: `python -m tools.mongo_to_parquet`
- Configure output via `.env` → `EXPORT_DIR`
- Use `--query`, `--limit`, `--chunk`, `--out` for control
- Parquet output is ML-ready and consistent across chunks

If you extend the schema (new fields in Mongo), try to keep types consistent and, if needed, update `normalize_document()` to ensure clean Parquet schemas.
