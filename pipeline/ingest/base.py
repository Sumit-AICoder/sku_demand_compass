"""Connector contract: try the real source, fall back to a calibrated synthesis.

Every connector declares both paths. The runner calls `run()`, which attempts
`fetch_real()` and, on any failure, logs why and calls `synthesize()`. Which path
ran is written to the fetch manifest and stamped on every row as `provenance`,
so nothing downstream can silently treat a simulated column as observed.

This is the mechanism behind the plan's rule that a dead endpoint degrades the
data rather than crashing the pipeline.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

import pandas as pd
import requests

from pipeline.common import Config, FetchRecord, Manifest, log


class Connector(ABC):
    #: key into pipeline/config/sources.yaml
    source_key: str = ""
    #: provenance to stamp when synthesize() is used
    sim_provenance: str = "simulated"

    def __init__(self, spine: dict[str, pd.DataFrame], seed: int = 20260822):
        self.spine = spine
        self.seed = seed
        self.log = log(f"ingest.{self.source_key}")
        self.meta = Config.sources()["sources"].get(self.source_key, {})

    # ---- the two paths a connector must offer ------------------------------

    def fetch_real(self) -> pd.DataFrame:
        """Fetch from the live source. Raise to signal 'not available'."""
        raise NotImplementedError

    @abstractmethod
    def synthesize(self) -> pd.DataFrame:
        """Generate a calibrated stand-in. Parameters must come from sim_params.yaml."""

    # ---- runner ------------------------------------------------------------

    def run(self) -> pd.DataFrame:
        t0 = time.time()
        access = self.meta.get("access", "gated")
        err = None

        if access in ("open_api", "keyed_api", "scrape"):
            try:
                df = self.fetch_real()
                if df is not None and len(df):
                    df["provenance"] = self.meta.get("provenance_if_real", "real")
                    self._record("real", df, time.time() - t0)
                    return df
                err = "empty response"
            except NotImplementedError:
                err = "real connector not implemented"
            except (requests.RequestException, ValueError, KeyError) as exc:
                err = f"{type(exc).__name__}: {exc}"[:200]
        else:
            err = f"access={access}: no scriptable machine-readable surface"

        self.log.info("synthesising (%s)", err)
        df = self.synthesize()
        df["provenance"] = self.meta.get("provenance_if_sim", self.sim_provenance)
        self._record("synthetic", df, time.time() - t0, err)
        return df

    def _record(self, mode: str, df: pd.DataFrame, elapsed: float, err: str | None = None) -> None:
        Manifest.record(FetchRecord(
            source=self.source_key, mode=mode, rows=len(df),
            provenance=str(df["provenance"].iloc[0]) if len(df) else "simulated",
            url=self.meta.get("url"), error=err, elapsed_s=round(elapsed, 2),
            vintage=self.meta.get("vintage"),
        ))
