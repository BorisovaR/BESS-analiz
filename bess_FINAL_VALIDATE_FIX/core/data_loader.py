"""
Energomonitor BESS - Data Loader & Import Wizard Core
PHASE 1: Handles CSV/XLSX, column mapping, unit conversion, validation
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import re

# Supported canonical fields
CANONICAL_FIELDS = [
    "timestamp",
    "consumption",
    "pv_production",
    "grid_import",
    "grid_export",
    "battery_charge",
    "battery_discharge",
    "soc",
    "buy_price",
    "sell_price"
]

# Heuristic patterns for auto-detection (Bulgarian + English)
FIELD_PATTERNS = {
    "timestamp": [r"time", r"date", r"timestamp", r"време", r"дата"],
    "consumption": [r"consum", r"load", r"потреб", r"консумация", r"товар", r"demand"],
    "pv_production": [r"pv", r"production", r"генерация", r"производство", r"фв", r"солар", r"solar", r"yield"],
    "grid_import": [r"import", r"внос", r"покупка", r"buy", r"grid.*in", r"от мрежа"],
    "grid_export": [r"export", r"отдаване", r"sell", r"износ", r"grid.*out", r"feed.*in"],
    "battery_charge": [r"charge", r"зареждане", r"bess.*charge"],
    "battery_discharge": [r"discharge", r"разреждане", r"bess.*dis"],
    "soc": [r"soc", r"state.*charge", r"заряд.*батерия"],
    "buy_price": [r"buy.*price", r"цена.*покупка", r"purchase.*price", r"цена.*внос"],
    "sell_price": [r"sell.*price", r"цена.*продажба", r"feed.*price", r"цена.*износ"],
}

UNIT_MULTIPLIERS_TO_KW = {
    "w": 0.001,
    "kw": 1.0,
    "mw": 1000.0,
    "kwh": None,  # energy - handled separately
    "mwh": None,
    "wh": None,
}

UNIT_MULTIPLIERS_TO_KWH = {
    "wh": 0.001,
    "kwh": 1.0,
    "mwh": 1000.0,
}

@dataclass
class DataQualityReport:
    total_rows: int
    start_date: Optional[pd.Timestamp]
    end_date: Optional[pd.Timestamp]
    resolution_minutes: Optional[float]
    missing_intervals: int
    duplicate_timestamps: int
    negative_values: Dict[str, int]
    nan_counts: Dict[str, int]
    anomalies: List[str] = field(default_factory=list)
    score: int = 100  # 0-100
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "total_rows": self.total_rows,
            "period_days": (self.end_date - self.start_date).days if self.start_date and self.end_date else 0,
            "resolution_min": self.resolution_minutes,
            "missing_intervals": self.missing_intervals,
            "duplicate_timestamps": self.duplicate_timestamps,
            "negative_values": self.negative_values,
            "nan_counts": self.nan_counts,
            "score": self.score,
            "warnings": self.warnings,
            "anomalies": self.anomalies
        }

def auto_detect_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """Return mapping canonical -> actual column name (best guess)"""
    mapping = {f: None for f in CANONICAL_FIELDS}
    cols_lower = {c: c.lower() for c in df.columns}
    
    # timestamp: try to find datetime column
    for col in df.columns:
        try:
            # if >80% parseable as datetime
            parsed = pd.to_datetime(df[col], errors='coerce')
            if parsed.notna().mean() > 0.8:
                mapping["timestamp"] = col
                break
        except:
            continue
    
    # other fields via patterns
    for canon, patterns in FIELD_PATTERNS.items():
        if canon == "timestamp":
            continue
        for actual_col, lower in cols_lower.items():
            if mapping[canon] is not None:
                continue
            for pat in patterns:
                if re.search(pat, lower):
                    # avoid double mapping same column to two fields unless timestamp
                    if actual_col not in mapping.values() or actual_col == mapping.get("timestamp"):
                        mapping[canon] = actual_col
                        break
    return mapping

def load_raw_file(file, filename: str) -> pd.DataFrame:
    """Load CSV or XLSX to DataFrame"""
    if filename.lower().endswith(".csv"):
        # try common delimiters and encodings
        try:
            return pd.read_csv(file, sep=None, engine='python')
        except:
            file.seek(0)
            return pd.read_csv(file)
    elif filename.lower().endswith((".xlsx",".xls")):
        return pd.read_excel(file)
    else:
        raise ValueError("Поддържат се само CSV и XLSX файлове")

def normalize_data(df: pd.DataFrame, column_mapping: Dict[str, str], units: Dict[str, str]) -> Tuple[pd.DataFrame, DataQualityReport, List[str]]:
    """
    column_mapping: canonical -> actual col name (as selected by user)
    units: canonical -> unit string e.g. 'kW', 'kWh', 'MW', 'MWh'
    Returns normalized df with canonical columns in kW or kWh appropriately converted to kWh energy?
    PHASE 1 rule: we keep power columns as kW, but energy_balance will convert to energy.
    For simplicity, we standardize all power/energy fields to kW if unit is power, and to kWh as energy per interval? 
    Actually we store as kW for power, but also keep energy_kwh computed later.
    Here we convert to kW for power-type and kWh for energy-type raw values normalized to kW/kWh base.
    """
    warnings = []
    # Build working df
    work = pd.DataFrame()
    
    # timestamp
    ts_col = column_mapping.get("timestamp")
    if not ts_col:
        raise ValueError("Не е избрана колона за време (timestamp)")
    work["timestamp"] = pd.to_datetime(df[ts_col], errors='coerce')
    if work["timestamp"].isna().mean() > 0.2:
        raise ValueError("Колоната за време съдържа твърде много невалидни дати")
    work = work.dropna(subset=["timestamp"])
    work = work.sort_values("timestamp")
    
    # deduplicate - keep first
    dup_count = work.duplicated(subset=["timestamp"]).sum()
    work = work.drop_duplicates(subset=["timestamp"], keep='first')
    
    # detect resolution
    if len(work) > 1:
        diffs = work["timestamp"].diff().dropna().dt.total_seconds() / 60
        median_res = diffs.median()
    else:
        median_res = None

    # interval_hours for conversion later
    interval_hours = (median_res / 60.0) if median_res else 0.25

    # Process each canonical field
    for canon in CANONICAL_FIELDS:
        if canon == "timestamp":
            continue
        actual = column_mapping.get(canon)
        if not actual:
            continue
        if actual not in df.columns:
            continue
        unit = units.get(canon, "kW").lower()
        raw_series = pd.to_numeric(df[actual], errors='coerce')
        # Align with work's index after timestamp filtering? Use original df order but we sorted work.
        # Reindex via timestamp merge is safer: merge on timestamp
        temp = pd.DataFrame({"timestamp": pd.to_datetime(df[ts_col], errors='coerce'), canon: raw_series})
        temp = temp.dropna(subset=["timestamp"])
        temp = temp.drop_duplicates(subset=["timestamp"], keep='first')
        work = work.merge(temp, on="timestamp", how="left")

        # Unit conversion
        if unit in ["kw","mw","w"]:
            mult = UNIT_MULTIPLIERS_TO_KW.get(unit)
            if mult is not None:
                work[canon] = work[canon] * mult  # now kW
                work[f"{canon}_unit_type"] = "power"
        elif unit in ["kwh","mwh","wh"]:
            mult = UNIT_MULTIPLIERS_TO_KWH.get(unit)
            if mult is not None:
                work[canon] = work[canon] * mult  # now kWh per interval as energy
                work[f"{canon}_unit_type"] = "energy"
        else:
            work[f"{canon}_unit_type"] = "unknown"

    # Validation
    negative_values = {}
    nan_counts = {}
    anomalies = []

    for canon in CANONICAL_FIELDS:
        if canon == "timestamp":
            continue
        if canon in work.columns:
            neg = (work[canon] < 0).sum()
            if neg > 0:
                negative_values[canon] = int(neg)
                # For consumption/PV, negative is suspicious
                if canon in ["consumption","pv_production"] and neg > len(work)*0.01:
                    anomalies.append(f"{canon}: {neg} отрицателни стойности")
            nan_counts[canon] = int(work[canon].isna().sum())

    # Missing intervals detection
    missing_intervals = 0
    if median_res and len(work) > 1:
        expected_range = pd.date_range(start=work["timestamp"].min(), end=work["timestamp"].max(), freq=f"{int(median_res)}min")
        missing_intervals = len(expected_range) - len(work)
        if missing_intervals > 0:
            warnings.append(f"Липсват {missing_intervals} интервала от очакваните (дупки в данните)")
            if missing_intervals > len(expected_range)*0.05:
                anomalies.append(f"Над 5% липсващи интервали ({missing_intervals})")

    # Resolution warning
    if median_res and median_res not in [15,30,60]:
        if abs(median_res - 15) > 2:
            warnings.append(f"Данните са с резолюция {median_res:.0f} минути, а не 15 мин. Симулацията ще работи с наличната резолюция, но точността е по-ниска.")

    # Period warning
    if work["timestamp"].max() - work["timestamp"].min() < pd.Timedelta(days=30):
        warnings.append("Периодът е под 1 месец - резултатите са с ограничена представителност")
    elif work["timestamp"].max() - work["timestamp"].min() < pd.Timedelta(days=330):
        warnings.append("Периодът е под 12 месеца - сезонността не е напълно обхваната")

    # Data quality score
    score = 100
    score -= min(30, missing_intervals / max(1,len(work)) * 200)
    score -= min(20, dup_count / max(1,len(work)) * 500)
    score -= min(20, sum(nan_counts.values()) / max(1,len(work)*len(nan_counts)) * 100)
    score -= len(anomalies)*5
    score = max(0, min(100, int(score)))

    report = DataQualityReport(
        total_rows=len(work),
        start_date=work["timestamp"].min(),
        end_date=work["timestamp"].max(),
        resolution_minutes=median_res,
        missing_intervals=int(missing_intervals),
        duplicate_timestamps=int(dup_count),
        negative_values=negative_values,
        nan_counts=nan_counts,
        anomalies=anomalies,
        score=score,
        warnings=warnings
    )

    # Keep only canonical columns + timestamp + unit_type meta
    keep_cols = ["timestamp"] + [c for c in CANONICAL_FIELDS if c in work.columns and c != "timestamp"]
    meta_cols = [c for c in work.columns if c.endswith("_unit_type")]
    normalized = work[["timestamp"] + keep_cols[1:] + meta_cols].copy()
    normalized = normalized.sort_values("timestamp").reset_index(drop=True)

    return normalized, report, warnings

def get_interval_hours(timestamps: pd.Series) -> pd.Series:
    """Return interval in hours for each row (based on diff to next)"""
    diffs = timestamps.diff().shift(-1)  # time to next
    # for last row use median
    median = diffs.median()
    diffs = diffs.fillna(median)
    return diffs.dt.total_seconds() / 3600.0
