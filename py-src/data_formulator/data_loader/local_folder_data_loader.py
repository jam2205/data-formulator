# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Local folder data loader — reads data files from a directory on the local filesystem.

Only available in local deployment mode (backend bound to localhost).
Uses ConfinedDir to ensure all file access stays within the connected root directory.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq

from data_formulator.data_loader.external_data_loader import (
    ExternalDataLoader,
    CatalogNode,
    MAX_IMPORT_ROWS,
    build_source_filter_where_clause_inline,
)
from data_formulator.data_loader import probe_utils
from data_formulator.datalake.parquet_utils import df_to_safe_records
from data_formulator.security.path_safety import ConfinedDir

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({
    ".csv", ".tsv", ".parquet",
    ".json", ".jsonl",
    ".xlsx", ".xls",
})


class LocalFolderDataLoader(ExternalDataLoader):
    """Browse and import data files from a local directory."""

    DISPLAY_NAME = "Local Folder"

    @staticmethod
    def list_params() -> list[dict[str, Any]]:
        return [
            {
                "name": "root_dir",
                "type": "string",
                "required": True,
                "default": "",
                "tier": "connection",
                "description": "Absolute path to the local directory to browse",
            },
            {
                "name": "recursive",
                "type": "boolean",
                "required": False,
                "default": "true",
                "tier": "connection",
                "advanced": True,
                "description": "Include files in subdirectories",
            },
            {
                "name": "file_pattern",
                "type": "string",
                "required": False,
                "default": "",
                "tier": "connection",
                "advanced": True,
                "description": "Glob pattern to filter files (e.g. '*.csv')",
            },
        ]

    AUTH_GUIDE = "local_folder.md"

    @staticmethod
    def catalog_hierarchy() -> list[dict[str, str]]:
        return [
            {"key": "folder", "label": "Folder"},
            {"key": "table", "label": "File"},
        ]

    def __init__(self, params: dict[str, Any]):
        self.params = params
        raw_root = params.get("root_dir", "") or ""
        # Expand ~ and environment variables (e.g. $HOME, %USERPROFILE%) so users
        # can paste shell-style paths into the connect dialog.
        expanded = os.path.expandvars(os.path.expanduser(raw_root))
        self.root_dir = Path(expanded).resolve()
        recursive_val = params.get("recursive", True)
        if isinstance(recursive_val, str):
            self.recursive = recursive_val.lower() not in ("false", "0", "no")
        else:
            self.recursive = bool(recursive_val)
        self.file_pattern = params.get("file_pattern", "")
        self._jail: ConfinedDir | None = None

    def test_connection(self) -> bool:
        """Validate the root directory exists and is readable."""
        try:
            if not self.root_dir.is_dir():
                return False
            # Verify we can list the directory
            next(self.root_dir.iterdir(), None)
            self._jail = ConfinedDir(self.root_dir, mkdir=False)
            return True
        except (PermissionError, OSError):
            return False

    # -- Catalog tree API --------------------------------------------------

    def ls(
        self,
        path: list[str] | None = None,
        filter: str | None = None,
    ) -> list[CatalogNode]:
        """List children at a catalog path.

        path=[] → list top-level folders and files.
        path=["subfolder"] → list contents of subfolder.
        """
        path = path or []
        eff = self.effective_hierarchy()
        if len(path) >= len(eff):
            return []

        # Navigate to the target directory
        if path:
            try:
                target = self._jail / "/".join(path)
            except ValueError:
                return []
            if not target.is_dir():
                return []
        else:
            target = self.root_dir

        nodes: list[CatalogNode] = []
        try:
            children = sorted(target.iterdir())
        except PermissionError:
            return []

        for child in children:
            if child.name.startswith("."):
                continue

            rel_parts = list(child.relative_to(self.root_dir).parts)

            if child.is_dir():
                if filter and filter.lower() not in child.name.lower():
                    continue
                nodes.append(CatalogNode(
                    name=child.name,
                    node_type="namespace",
                    path=rel_parts,
                ))
            elif child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
                if self.file_pattern and not child.match(self.file_pattern):
                    continue
                if filter and filter.lower() not in child.name.lower():
                    continue
                nodes.append(CatalogNode(
                    name=child.name,
                    node_type="table",
                    path=rel_parts,
                    metadata=self._file_metadata(child),
                ))

        return nodes

    def get_metadata(self, path: list[str]) -> dict[str, Any]:
        """Get detailed metadata for a single file, including sample rows."""
        if not path:
            return {}
        try:
            resolved = self._jail / "/".join(path)
        except ValueError:
            return {}
        if not resolved.is_file():
            return {}

        meta = self._file_metadata(resolved)

        # Read a small sample for preview
        try:
            table = self.fetch_data_as_arrow("/".join(path), {"size": 5})
            sample_df = table.to_pandas()
            meta["columns"] = [
                {"name": c, "type": str(sample_df[c].dtype)}
                for c in sample_df.columns
            ]
            meta["sample_rows"] = df_to_safe_records(sample_df)
            meta["row_count"] = meta.get("row_count") or len(sample_df)
        except Exception as exc:
            logger.debug("Sample read failed for %s: %s", path, exc)

        return meta

    def list_tables(self, table_filter: str | None = None) -> list[dict[str, Any]]:
        """Return data files as 'tables', with subdirectories as namespaces."""
        if self._jail is None:
            self._jail = ConfinedDir(self.root_dir, mkdir=False)

        results: list[dict[str, Any]] = []
        pattern = self.file_pattern or "*"

        if self.recursive:
            candidates = self.root_dir.rglob(pattern)
        else:
            candidates = self.root_dir.glob(pattern)

        for filepath in sorted(candidates):
            if not filepath.is_file():
                continue
            if filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if filepath.name.startswith("."):
                continue

            rel = filepath.relative_to(self.root_dir)
            name = str(rel)

            if table_filter and table_filter.lower() not in name.lower():
                continue

            metadata = self._file_metadata(filepath)
            results.append({
                "name": name,
                "metadata": metadata,
                "path": list(rel.parts),
            })

        return results

    def fetch_data_as_arrow(
        self,
        source_table: str,
        import_options: dict[str, Any] | None = None,
    ) -> pa.Table:
        """Read a file from the connected folder into an Arrow table."""
        if self._jail is None:
            self._jail = ConfinedDir(self.root_dir, mkdir=False)

        resolved = self._jail / source_table
        opts = import_options or {}
        # Match every other loader: default to the shared cap and clamp to
        # it. This one defaulted to 1_000_000 and clamped to nothing, so a
        # file larger than that imported truncated with no signal, and a
        # caller asking for more than MAX_IMPORT_ROWS was simply obeyed.
        size = min(opts.get("size", MAX_IMPORT_ROWS), MAX_IMPORT_ROWS)

        ext = resolved.suffix.lower()
        source_filters = opts.get("source_filters") or []
        parquet_total: int | None = None
        if ext == ".parquet" and source_filters:
            # Filtering has to happen INSIDE the read. Without this the loader
            # takes the first `size` rows of the file and the caller filters
            # afterwards, which on an interleaved column store is not a subset
            # of the rows they asked for -- measured on candles_1h.parquet
            # (4,075,042 rows), the first 2,000,000 hold between 19% (NZDCAD)
            # and 62% (USDCAD) of each pair, with gaps mid-history and no
            # error. DuckDB pushes the predicate into the parquet scan, so what
            # comes back is every matching row up to `size`.
            import duckdb

            where = build_source_filter_where_clause_inline(
                source_filters, quote_char='"', dialect="duckdb",
            )
            path_lit = str(resolved).replace("'", "''")
            con = duckdb.connect()
            try:
                parquet_total = con.execute(
                    f"SELECT count(*) FROM read_parquet('{path_lit}') {where}"
                ).fetchone()[0]
                # fetch_arrow_table, not .arrow(): the latter hands back a
                # RecordBatchReader on current duckdb, not a Table.
                table = con.execute(
                    f"SELECT * FROM read_parquet('{path_lit}') {where} LIMIT {int(size)}"
                ).fetch_arrow_table()
            finally:
                con.close()
        elif ext == ".parquet":
            # Read only as far as `size`. pq.read_table materialises the whole
            # file and the slice below then throws most of it away, which is
            # cheap for a spreadsheet-sized export and expensive for a real
            # column store: measured on a 505 MB / 13,538,882-row parquet, that
            # path peaked at 2.6 GB RSS to keep the 118 MB the caller asked for
            # -- 14x more data read than returned. iter_batches stops at the
            # first row group that satisfies `size`, and the honest total row
            # count comes from the footer, so reporting it no longer costs a
            # full read either.
            pf = pq.ParquetFile(str(resolved))
            parquet_total = pf.metadata.num_rows
            batches = []
            taken = 0
            for batch in pf.iter_batches():
                batches.append(batch)
                taken += batch.num_rows
                if taken >= size:
                    break
            table = (
                pa.Table.from_batches(batches, schema=pf.schema_arrow)
                if batches
                else pf.schema_arrow.empty_table()
            )
        elif ext in (".csv", ".tsv"):
            # ``.tsv`` is tab-separated; pyarrow's read_csv defaults to a comma
            # delimiter, so without this a TSV collapses into a single column
            # (e.g. "id\trate" stays one field). Keep comma for ``.csv``.
            parse_options = (
                pa_csv.ParseOptions(delimiter="\t") if ext == ".tsv" else None
            )
            table = pa_csv.read_csv(str(resolved), parse_options=parse_options)
        elif ext in (".json", ".jsonl"):
            import pyarrow.json as pa_json
            table = pa_json.read_json(str(resolved))
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(str(resolved))
            table = pa.Table.from_pandas(df)
        else:
            raise ValueError(f"Unsupported file type: {ext}")

        # Store total before slicing so callers can get the real count. For
        # parquet that is the footer's count, not what we chose to read.
        self._last_total_rows = (
            parquet_total if parquet_total is not None else table.num_rows
        )

        if table.num_rows > size:
            table = table.slice(0, size)

        # Truncation is otherwise silent: the import route reports the rows
        # it kept, not the rows the file holds.
        if self._last_total_rows and self._last_total_rows > table.num_rows:
            logger.warning(
                "Truncated %s: kept %d of %d rows (size=%d). Filter at the "
                "source or raise `size` if you need the rest.",
                source_table, table.num_rows, self._last_total_rows, size,
            )

        logger.info(
            "Fetched %d rows from local file: %s",
            table.num_rows, source_table,
        )
        return table

    def probe(self, path: list[str], query: dict[str, Any]) -> dict[str, Any]:
        """Read the file into DuckDB and compute the SPJQ there."""
        return probe_utils.run_probe_on_duckdb(self, path, query, scan_size=MAX_IMPORT_ROWS)

    # -- Helpers -----------------------------------------------------------

    def _file_metadata(self, filepath: Path) -> dict[str, Any]:
        """Extract lightweight metadata without reading the full file."""
        ext = filepath.suffix.lower()
        try:
            stat = filepath.stat()
        except OSError:
            return {}

        meta: dict[str, Any] = {
            "file_size": stat.st_size,
            "modified": stat.st_mtime,
            "file_type": ext.lstrip("."),
        }

        try:
            if ext == ".parquet":
                pf = pq.ParquetFile(str(filepath))
                meta["row_count"] = pf.metadata.num_rows
                schema = pf.schema_arrow
                meta["columns"] = [
                    {"name": schema.field(i).name, "type": str(schema.field(i).type)}
                    for i in range(len(schema))
                ]
            elif ext in (".csv", ".tsv"):
                with open(filepath, "r", errors="replace") as f:
                    header = f.readline().strip()
                sep = "\t" if ext == ".tsv" else ","
                meta["columns"] = [
                    {"name": c.strip().strip('"'), "type": "string"}
                    for c in header.split(sep)
                    if c.strip()
                ]
                meta["row_count"] = None
            elif ext in (".json", ".jsonl"):
                with open(filepath, "r", errors="replace") as f:
                    first_line = f.readline().strip()
                if first_line:
                    try:
                        obj = json.loads(first_line)
                        if isinstance(obj, dict):
                            meta["columns"] = [
                                {"name": k, "type": type(v).__name__}
                                for k, v in obj.items()
                            ]
                        elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
                            meta["columns"] = [
                                {"name": k, "type": type(v).__name__}
                                for k, v in obj[0].items()
                            ]
                    except json.JSONDecodeError:
                        pass
                meta["row_count"] = None
            elif ext in (".xlsx", ".xls"):
                meta["row_count"] = None
        except Exception as exc:
            logger.debug("Metadata extraction failed for %s: %s", filepath, exc)

        return meta
