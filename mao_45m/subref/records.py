"""Rows of the database tables written by the subreflector control.

Each dataclass here is one row of a table in monitor/init.sql: the field
names are the column names, and the values are already in the units the
table uses (times in s, lengths in m, frequencies in Hz).
mao_45m.database writes them as they are, so all conversion from the
control loop's own types happens here.

"""

__all__ = ["Control", "Run"]


# standard library
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any


# dependencies
import numpy as np
from typing_extensions import Self
from ..utils import to_timedelta
if TYPE_CHECKING:
    import xarray as xr
    from ..cosmos import State


# constants
SECOND = np.timedelta64(1, "s")

@dataclass(frozen=True)
class Control:
    """One row of the control table: one control period.

    EPLs and subreflector offsets are in m. A feed that is not in the
    feed pattern is None, and an EPL that could not be estimated is NaN;
    both are stored as NULL.

    """

    time: datetime | None  # last VDIF sample of the EPL integration (UTC)
    cosmos_time: datetime
    cal_time: datetime | None
    epl_c: float | None  # m
    epl_t: float | None  # m
    epl_r: float | None  # m
    epl_b: float | None  # m
    epl_l: float | None  # m
    dx: float  # m
    dz: float  # m
    sent: bool

    @classmethod
    def from_cycle(
        cls,
        *,
        state: "State",
        epl: "xr.DataArray",
        epl_cal: "xr.DataArray",
        subref: "xr.DataArray",
        sent: bool,
    ) -> Self:
        """Create the record of one control period.

        Args:
            state: Telescope state received in this period.
            epl: Estimated EPL (in m; feed).
            epl_cal: Estimated EPL at calibration (in m; feed).
                Only its time is stored.
            subref: Subreflector parameters (in m; drive).
            sent: Whether the parameters were sent to COSMOS
                (False in a dry run).

        """
        feeds = set(epl.feed.values.tolist())

        def get_epl(feed: str, /) -> float | None:
            return float(epl.sel(feed=feed)) if feed in feeds else None

        return cls(
            time=get_time(epl),
            cosmos_time=state.time,
            cal_time=get_time(epl_cal),
            epl_c=get_epl("c"),
            epl_t=get_epl("t"),
            epl_r=get_epl("r"),
            epl_b=get_epl("b"),
            epl_l=get_epl("l"),
            dx=float(subref.sel(drive="X")),
            dz=float(subref.sel(drive="Z")),
            sent=sent,
        )
@dataclass(frozen=True)
class Run:
    """One row of the run table: the settings of one control run.

    Each (min, max) argument of control() is split into <name>_min and
    <name>_max. finished_at and error are filled in when the run ends;
    error stays None for a normal end or Ctrl+C.

    """

    started_at: datetime
    # EPL estimates
    cal_interval: float  # s
    freq_binning: int
    freq_range_min: float  # Hz
    freq_range_max: float  # Hz
    integ_per_sample: float  # s
    integ_per_epl: float  # s
    # subreflector control
    dry_run: bool
    control_duration: float  # s
    control_origin: datetime
    control_period: float  # s
    epl_interval_tolerance: float
    epl_offset_schedule: str | None
    integral_gain_dx: float
    integral_gain_dz: float
    proportional_gain_dx: float
    proportional_gain_dz: float
    anti_windup_gain_dx: float
    anti_windup_gain_dz: float
    range_dx_min: float  # m
    range_dx_max: float  # m
    range_dz_min: float  # m
    range_dz_max: float  # m
    range_ddx_min: float  # m
    range_ddx_max: float  # m
    range_ddz_min: float  # m
    range_ddz_max: float  # m
    # end of the run
    finished_at: datetime | None = None
    error: str | None = None

    @classmethod
    def from_args(cls, args: dict[str, Any], /) -> Self:
        """Create the record of a run starting now from control() arguments.

        Args:
            args: Arguments of control(), with control_origin already
                converted to a datetime64 (naive UTC).

        """
        schedule = args["epl_offset_schedule"]

        return cls(
            started_at=datetime.now(timezone.utc),
            cal_interval=to_seconds(args["cal_interval"]),
            freq_binning=int(args["freq_binning"]),
            freq_range_min=float(args["freq_range"][0]),
            freq_range_max=float(args["freq_range"][1]),
            integ_per_sample=to_seconds(args["integ_per_sample"]),
            integ_per_epl=to_seconds(args["integ_per_epl"]),
            dry_run=bool(args["dry_run"]),
            control_duration=to_seconds(args["control_duration"]),
            control_origin=to_utc(args["control_origin"]),
            control_period=to_seconds(args["control_period"]),
            epl_interval_tolerance=float(args["epl_interval_tolerance"]),
            epl_offset_schedule=None if schedule is None else str(schedule),
            integral_gain_dx=float(args["integral_gain_dX"]),
            integral_gain_dz=float(args["integral_gain_dZ"]),
            proportional_gain_dx=float(args["proportional_gain_dX"]),
            proportional_gain_dz=float(args["proportional_gain_dZ"]),
            anti_windup_gain_dx=float(args["anti_windup_gain_dX"]),
            anti_windup_gain_dz=float(args["anti_windup_gain_dZ"]),
            range_dx_min=float(args["range_dX"][0]),
            range_dx_max=float(args["range_dX"][1]),
            range_dz_min=float(args["range_dZ"][0]),
            range_dz_max=float(args["range_dZ"][1]),
            range_ddx_min=float(args["range_ddX"][0]),
            range_ddx_max=float(args["range_ddX"][1]),
            range_ddz_min=float(args["range_ddZ"][0]),
            range_ddz_max=float(args["range_ddZ"][1]),
        )

def get_time(da: "xr.DataArray", /) -> datetime | None:
    """Return the time coordinate (naive UTC) as an aware datetime."""
    if "time" not in da.coords:
        return None

    # datetime64[ns].item() gives an int, so go through [us] first
    value = da.coords["time"].values.astype("datetime64[us]").item()
    return None if value is None else value.replace(tzinfo=timezone.utc)

def to_seconds(value: Any, /) -> float:
    """Convert a time length (e.g. "10 s", "2 hr" or 0.5) to seconds."""
    return float(to_timedelta(value) / SECOND)


def to_utc(value: Any, /) -> datetime:
    """Convert a naive UTC datetime64 to a timezone-aware datetime."""
    return np.datetime64(value, "us").item().replace(tzinfo=timezone.utc)