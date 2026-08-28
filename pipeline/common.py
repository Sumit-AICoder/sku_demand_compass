"""Shared plumbing: paths, config loading, provenance tracking, fetch manifest.

Every stage of the pipeline reads config through here and writes parquet through
`write_table`, which enforces that a provenance column exists. Provenance is not
decoration -- the dashboard renders a confidence badge off it, and Phase 5 refuses
to weight a factor whose inputs are entirely simulated.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "pipeline" / "config"
DATA = ROOT / "data"
RAW, CURATED, MARTS = DATA / "raw", DATA / "curated", DATA / "marts"
for _d in (RAW, CURATED, MARTS):
    _d.mkdir(parents=True, exist_ok=True)

# Provenance vocabulary, ordered weakest -> strongest evidence.
PROVENANCE = ("simulated", "allocated", "real")

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)-5s %(name)-22s %(message)s",
    datefmt="%H:%M:%S",
)


def log(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------- config


def load_yaml(name: str) -> dict:
    with open(CONFIG / name) as fh:
        return yaml.safe_load(fh)


class Config:
    """Lazily-loaded, cached view over the YAML config files."""

    _cache: dict[str, Any] = {}

    @classmethod
    def _get(cls, name: str) -> dict:
        if name not in cls._cache:
            cls._cache[name] = load_yaml(name)
        return cls._cache[name]

    @classmethod
    def sources(cls) -> dict:
        return cls._get("sources.yaml")

    @classmethod
    def factors(cls) -> dict:
        return cls._get("factors.yaml")["factors"]

    @classmethod
    def weights(cls) -> dict:
        return cls._get("weights.yaml")

    @classmethod
    def skus(cls) -> list[dict]:
        return cls._get("sku_catalog.yaml")["skus"]

    @classmethod
    def sku_categories(cls) -> dict:
        return cls._get("sku_catalog.yaml")["categories"]

    @classmethod
    def sku(cls, sku_id: str) -> dict:
        for s in cls.skus():
            if s["id"] == sku_id:
                return s
        raise KeyError(sku_id)

    @classmethod
    def sim(cls) -> dict:
        return cls._get("sim_params.yaml")

    @classmethod
    def ucm(cls) -> dict:
        return cls._get("ucm.yaml")

    @classmethod
    def pilot_states(cls) -> list[dict]:
        return cls.sources()["pilot_states"]

    @classmethod
    def districts(cls) -> pd.DataFrame:
        if "_districts" not in cls._cache:
            cls._cache["_districts"] = pd.read_csv(CONFIG / "districts.csv")
        return cls._cache["_districts"].copy()


# ---------------------------------------------------------------- io


def write_table(df: pd.DataFrame, path: Path, *, provenance_required: bool = True) -> Path:
    """Write parquet, enforcing the provenance contract."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if provenance_required and "provenance" not in df.columns:
        raise ValueError(
            f"{path.name}: every table must carry a `provenance` column "
            f"(one of {PROVENANCE}). Refusing to write untraceable data."
        )
    if "provenance" in df.columns:
        bad = set(df["provenance"].dropna().unique()) - set(PROVENANCE)
        if bad:
            raise ValueError(f"{path.name}: unknown provenance values {bad}")
    df.to_parquet(path, index=False)
    log("io").info("wrote %-38s %7d rows x %2d cols", path.name, len(df), df.shape[1])
    return path


def read_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing -- run the earlier pipeline stage first "
            f"(python -m pipeline.run --stage <name>)"
        )
    return pd.read_parquet(path)


# ---------------------------------------------------------------- manifest


@dataclass
class FetchRecord:
    source: str
    mode: str            # "real" | "synthetic"
    rows: int
    provenance: str
    url: str | None = None
    vintage: str | None = None
    coverage_pct: float | None = None
    error: str | None = None
    elapsed_s: float | None = None
    fetched_at: str | None = None


class Manifest:
    """Append-only record of what each connector actually did.

    This is what makes the real/simulated split auditable rather than a claim.
    """

    path = RAW / "_manifest.json"

    @classmethod
    def load(cls) -> dict[str, dict]:
        if cls.path.exists():
            return json.loads(cls.path.read_text())
        return {}

    @classmethod
    def record(cls, rec: FetchRecord) -> None:
        rec.fetched_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        data = cls.load()
        data[rec.source] = {k: v for k, v in asdict(rec).items() if v is not None}
        cls.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def summary(cls) -> pd.DataFrame:
        data = cls.load()
        if not data:
            return pd.DataFrame(columns=["source", "mode", "rows", "provenance"])
        return pd.DataFrame(
            [{"source": k, **v} for k, v in sorted(data.items())]
        )


# ---------------------------------------------------------------- numeric helpers


def pct_rank(s: pd.Series, *, invert: bool = False) -> pd.Series:
    """Percentile-rank to 0-100. Used to normalise every sub-factor.

    Ranking (not min-max) keeps a single outlier district from flattening the
    rest of the distribution, which matters because several of these inputs are
    heavy-tailed (holding size, dealer distance, reservoir storage).
    """
    r = s.rank(pct=True, na_option="keep") * 100.0
    if invert:
        r = 100.0 - r
    return r


def weakest_provenance(values: Iterable[str]) -> str:
    """A composite is only as trustworthy as its weakest input."""
    vals = [v for v in values if v in PROVENANCE]
    if not vals:
        return "simulated"
    return min(vals, key=PROVENANCE.index)


def clip01(s: pd.Series | float):
    if isinstance(s, pd.Series):
        return s.clip(0.0, 1.0)
    return max(0.0, min(1.0, s))
