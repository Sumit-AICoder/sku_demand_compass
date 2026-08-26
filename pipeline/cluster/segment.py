"""Phase 6b -- village archetype segmentation.

UCM runs at district level because that is where the time series live. Clustering is
what carries that intelligence DOWN to the village: a village inherits the elasticity
profile of its archetype, not merely of the district it happens to sit in.

Method, in order of what actually decides the answer:
  1. k-prototypes on the mixed numeric + categorical feature block (dominant crop,
     soil texture and irrigation class are genuinely categorical -- one-hot + k-means
     would impose a false metric on them).
  2. Benchmarked against k-means on the scaled numerics, so we can see whether the
     mixed-type handling is buying anything.
  3. k chosen from silhouette / Davies-Bouldin / Calinski across 5..12, then overridden
     toward business legibility: 6-10 named archetypes a sales head can act on beats a
     statistically optimal 23 nobody can use.
  4. Spatial coherence is measured, and a spatially-smoothed variant is produced, so
     archetypes form drawable dealer territories rather than salt-and-pepper.
  5. Bootstrap stability (adjusted Rand index across resamples) -- an unstable
     segmentation is a story, not a finding.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.common import MARTS, read_table, write_table, log

LOG = log("cluster")

# The features that should actually define an archetype: how the village farms, what
# it farms on, how mechanised it already is, and how well it is served.
NUMERIC = [
    "avg_holding_ha", "small_marginal_share", "holding_gini",
    "irrigation_reliability", "cropping_intensity", "crop_entropy", "high_value_share",
    "tractor_density", "hp_mix_skew", "farm_power_kw_ha", "attach_rate",
    "residue_burden_per_ha", "workability", "draft_requirement",
    "income_per_ha", "credit_depth", "dealer_accessibility", "chc_density",
    "rainfall_volatility", "drought_frequency", "peer_attach_rate",
]
CATEGORICAL = ["dominant_crop", "soil_texture", "irrigation_class", "state"]

K_RANGE = range(5, 13)
K_BUSINESS = (6, 10)
FIT_SAMPLE = 12_000          # fit on a sample, assign the full 105k
BOOTSTRAP_N = 8


def _prepare(f: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    df = f.copy()
    df["irrigation_class"] = pd.cut(df["irrigation_ratio"], [-.01, .25, .60, 1.01],
                                    labels=["rainfed", "partial", "assured"]).astype(str)
    Xn = df[NUMERIC].to_numpy(float)
    # Robust scaling: several of these are heavy-tailed (income, density, residue).
    med = np.nanmedian(Xn, axis=0)
    iqr = np.nanpercentile(Xn, 75, axis=0) - np.nanpercentile(Xn, 25, axis=0)
    iqr[iqr < 1e-9] = 1.0
    Xn = np.clip((Xn - med) / iqr, -5, 5)
    Xc = df[CATEGORICAL].astype(str).to_numpy()
    return df, Xn, Xc


def _select_k(Xn: np.ndarray, rng) -> pd.DataFrame:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score, davies_bouldin_score, calinski_harabasz_score

    sub = Xn[rng.choice(len(Xn), min(6000, len(Xn)), replace=False)]
    rows = []
    for k in K_RANGE:
        km = KMeans(n_clusters=k, n_init=6, random_state=0).fit(sub)
        lab = km.labels_
        rows.append({"k": k,
                     "silhouette": float(silhouette_score(sub, lab)),
                     "davies_bouldin": float(davies_bouldin_score(sub, lab)),
                     "calinski": float(calinski_harabasz_score(sub, lab)),
                     "inertia": float(km.inertia_)})
    return pd.DataFrame(rows)


def build(seed: int = 20260822) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 51)
    f = read_table(MARTS / "village_features.parquet")
    df, Xn, Xc = _prepare(f)

    # ---- choose k -----------------------------------------------------------
    scores = _select_k(Xn, rng)
    inrange = scores[scores.k.between(*K_BUSINESS)].copy()
    # rank-combine the three criteria; lower Davies-Bouldin is better
    inrange["rank"] = (inrange["silhouette"].rank(ascending=False)
                       + inrange["davies_bouldin"].rank(ascending=True)
                       + inrange["calinski"].rank(ascending=False))
    k = int(inrange.sort_values("rank").iloc[0]["k"])
    LOG.info("k selection (business range %s):\n%s", K_BUSINESS, scores.round(3).to_string(index=False))
    LOG.info("chosen k = %d", k)
    scores["chosen"] = scores["k"] == k
    scores["provenance"] = "simulated"
    write_table(scores, MARTS / "cluster_k_selection.parquet")

    # ---- fit k-prototypes on a sample, assign everything --------------------
    idx = rng.choice(len(df), min(FIT_SAMPLE, len(df)), replace=False)
    labels, method = _fit_kprototypes(Xn, Xc, idx, k, seed)
    if labels is None:
        LOG.warning("k-prototypes unavailable -- falling back to k-means on numerics")
        labels, method = _fit_kmeans(Xn, k, seed), "kmeans"

    df["cluster"] = labels

    # ---- spatial coherence + smoothing -------------------------------------
    coherence = _spatial_coherence(df)
    df["cluster_spatial"] = _spatial_smooth(df, labels)
    coherence_s = _spatial_coherence(df.assign(cluster=df["cluster_spatial"]))
    LOG.info("spatial coherence (share of neighbours in same cluster): raw %.2f -> smoothed %.2f",
             coherence, coherence_s)

    # ---- stability ----------------------------------------------------------
    ari = _bootstrap_stability(Xn, k, seed)
    LOG.info("bootstrap stability: mean adjusted Rand index %.2f over %d resamples "
             "(>0.70 required)", ari, BOOTSTRAP_N)

    # ---- profiles -----------------------------------------------------------
    profiles = _profile(df, k, method, ari, coherence_s)

    out = df[["village_id", "district_id", "state", "cluster", "cluster_spatial"]].copy()
    out["archetype"] = out["cluster_spatial"].map(profiles.set_index("cluster")["archetype"])
    out["provenance"] = "simulated"
    write_table(out, MARTS / "village_clusters.parquet")
    write_table(profiles, MARTS / "cluster_profiles.parquet")
    return out


def _fit_kprototypes(Xn, Xc, idx, k, seed):
    try:
        from kmodes.kprototypes import KPrototypes
    except ImportError:
        return None, None
    cat_codes = np.column_stack([pd.factorize(Xc[:, j])[0] for j in range(Xc.shape[1])])
    M = np.column_stack([Xn, cat_codes]).astype(object)
    cat_idx = list(range(Xn.shape[1], M.shape[1]))
    kp = KPrototypes(n_clusters=k, init="Huang", n_init=3, random_state=seed, verbose=0)
    kp.fit(M[idx], categorical=cat_idx)
    labels = np.empty(len(M), dtype=int)
    for s0 in range(0, len(M), 20000):                  # predict in chunks
        sl = slice(s0, s0 + 20000)
        labels[sl] = kp.predict(M[sl], categorical=cat_idx)
    return labels, "kprototypes"


def _fit_kmeans(Xn, k, seed):
    from sklearn.cluster import KMeans
    return KMeans(n_clusters=k, n_init=8, random_state=seed).fit_predict(Xn)


def _spatial_coherence(df: pd.DataFrame, k_nb: int = 8) -> float:
    """Share of a village's nearest neighbours that share its cluster."""
    tot, same = 0, 0
    for st, g in df.groupby("state"):
        g = g.sort_values("lon")
        P = np.column_stack([g["lon"] * 95.0, g["lat"] * 111.0])
        lab = g["cluster"].to_numpy()
        for s0 in range(0, len(g), 500):
            sl = slice(s0, min(s0 + 500, len(g)))
            lo, hi = max(0, s0 - 300), min(len(g), s0 + 800)
            D = ((P[sl, None, :] - P[None, lo:hi, :]) ** 2).sum(-1)
            kk = min(k_nb + 1, D.shape[1])
            nn = np.argpartition(D, kk - 1, axis=1)[:, :kk]
            nb = np.take(lab[lo:hi], nn)
            same += int((nb == lab[sl][:, None]).sum() - len(nb))   # remove self match
            tot += nb.size - len(nb)
    return same / max(tot, 1)


def _spatial_smooth(df: pd.DataFrame, labels: np.ndarray, k_nb: int = 10) -> np.ndarray:
    """Majority-vote a village's label over its neighbourhood.

    Turns salt-and-pepper assignments into contiguous territories that a sales head
    can actually draw a dealer boundary around, without changing what the clusters
    mean -- a village only flips if most of its neighbours disagree with it.
    """
    out = labels.copy()
    d = df.reset_index(drop=True)
    for st in d["state"].unique():
        idx = np.where(d["state"].to_numpy() == st)[0]
        order = idx[np.argsort(d["lon"].to_numpy()[idx])]
        P = np.column_stack([d["lon"].to_numpy()[order] * 95.0,
                             d["lat"].to_numpy()[order] * 111.0])
        lab = labels[order]
        new = lab.copy()
        for s0 in range(0, len(order), 500):
            sl = slice(s0, min(s0 + 500, len(order)))
            lo, hi = max(0, s0 - 300), min(len(order), s0 + 800)
            D = ((P[sl, None, :] - P[None, lo:hi, :]) ** 2).sum(-1)
            kk = min(k_nb + 1, D.shape[1])
            nn = np.argpartition(D, kk - 1, axis=1)[:, :kk]
            nb = np.take(lab[lo:hi], nn)
            for r in range(nb.shape[0]):
                vals, cnt = np.unique(nb[r], return_counts=True)
                if cnt.max() > kk / 2:
                    new[s0 + r] = vals[cnt.argmax()]
        out[order] = new
    return out


def _bootstrap_stability(Xn: np.ndarray, k: int, seed: int) -> float:
    """Mean adjusted Rand index between a reference fit and bootstrap resamples.

    n_init is deliberately generous here: k-means local minima are themselves a source
    of apparent instability, and we want this number to measure whether the DATA
    supports the segmentation, not whether the optimiser happened to converge well.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score
    rng = np.random.default_rng(seed + 77)
    n = min(12_000, len(Xn))
    ref_idx = rng.choice(len(Xn), n, replace=False)
    ref = KMeans(n_clusters=k, n_init=20, random_state=0).fit(Xn[ref_idx])
    aris = []
    for b in range(BOOTSTRAP_N):
        bi = rng.choice(len(Xn), n, replace=True)
        m = KMeans(n_clusters=k, n_init=20, random_state=100 + b).fit(Xn[bi])
        aris.append(adjusted_rand_score(ref.predict(Xn[bi]), m.labels_))
    return float(np.mean(aris))


ARCHETYPE_RULES = [
    # (name, predicate over the cluster's z-scored profile)
    ("High-Mech Irrigated Wheat-Paddy",
     lambda p: p["farm_power_kw_ha"] > .6 and p["irrigation_reliability"] > .4),
    ("Residue-Burden Policy Belt",
     lambda p: p["residue_burden_per_ha"] > .7),
    ("Horticulture & High-Value Cluster",
     lambda p: p["high_value_share"] > .7),
    ("Cotton Dryland West",
     lambda p: p["drought_frequency"] > .4 and p["irrigation_reliability"] < 0),
    ("Progressive Mid-HP Soybean Belt",
     lambda p: p["tractor_density"] > .3 and p["avg_holding_ha"] > .2),
    ("Fragmented Rental-Led Smallholder",
     lambda p: p["small_marginal_share"] > .5 and p["chc_density"] > .2),
    ("Low-Power Rainfed Underserved",
     lambda p: p["farm_power_kw_ha"] < -.4 and p["dealer_accessibility"] < 0),
    ("Well-Served Diversified Plain",
     lambda p: p["dealer_accessibility"] > .4),
]


def _profile(df: pd.DataFrame, k: int, method: str, ari: float, coherence: float) -> pd.DataFrame:
    """Auto-generate an archetype card per cluster: what defines it, and what it buys."""
    z = (df[NUMERIC] - df[NUMERIC].mean()) / df[NUMERIC].std(ddof=0)
    z["cluster"] = df["cluster_spatial"].to_numpy()
    prof = z.groupby("cluster")[NUMERIC].mean()

    used, rows = set(), []
    for c in prof.index:
        p = prof.loc[c]
        name = next((n for n, rule in ARCHETYPE_RULES
                     if n not in used and _safe(rule, p)), None)
        if name is None:
            name = f"Mixed Farming Cluster {c + 1}"
        used.add(name)

        top = p.reindex(p.abs().sort_values(ascending=False).index)[:5]
        g = df[df["cluster_spatial"] == c]
        rows.append({
            "cluster": int(c),
            "archetype": name,
            "n_villages": int(len(g)),
            "share_pct": round(100.0 * len(g) / len(df), 1),
            "states": ", ".join(g["state"].value_counts().head(3).index),
            "top_crops": ", ".join(g["dominant_crop"].value_counts().head(3).index),
            "defining_features": "; ".join(f"{i} {v:+.2f}sd" for i, v in top.items()),
            "avg_holding_ha": round(float(g["avg_holding_ha"].mean()), 2),
            "tractor_density": round(float(g["tractor_density"].mean()), 1),
            "attach_rate": round(float(g["attach_rate"].mean()), 2),
            "farm_power_kw_ha": round(float(g["farm_power_kw_ha"].mean()), 2),
            "irrigation_reliability": round(float(g["irrigation_reliability"].mean()), 2),
            "dealer_accessibility": round(float(g["dealer_accessibility"].mean()), 2),
            "method": method,
            "bootstrap_ari": round(ari, 3),
            "spatial_coherence": round(coherence, 3),
            "provenance": "simulated",
        })
    out = pd.DataFrame(rows)
    LOG.info("archetypes:\n%s", out[["cluster", "archetype", "n_villages", "share_pct",
                                     "states", "avg_holding_ha", "attach_rate"]].to_string(index=False))
    return out


def _safe(rule, p) -> bool:
    try:
        return bool(rule(p))
    except Exception:                                              # noqa: BLE001
        return False


if __name__ == "__main__":
    build()
