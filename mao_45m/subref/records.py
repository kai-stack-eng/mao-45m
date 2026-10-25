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
    from .convert import ControllerState


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
    # values of the controller (ControllerState) in m, or m s for v;
    # in a cycle where the controller held its output, these are from
    # an earlier cycle (the same values as in the row before)
    epl_offset_c: float | None
    epl_offset_t: float | None
    epl_offset_r: float | None
    epl_offset_b: float | None
    epl_offset_l: float | None
    q_c: float | None
    q_t: float | None
    q_r: float | None
    q_b: float | None
    q_l: float | None
    z_1: float # Z00: piston
    z_2: float # Z11: horizontal tilt
    z_3: float # Z1-1: vertical tilt
    z_4: float # Z20: defocus
    r_c: float | None
    r_t: float | None
    r_r: float | None
    r_b: float | None
    r_l: float | None
    r_l: float | None
    xi_x: float
    xi_z: float
    d_x: float
    d_z: float
    v_x: float # m s
    v_z: float # m s
    p_x: float
    p_z: float

    @classmethod
    def from_cycle(
        cls,
        *,
        state: "State",
        epl: "xr.DataArray",
        epl_cal: "xr.DataArray",
        subref: "ControllerState",
        feed_order: list[str],
        sent: bool,
    ) -> Self:
        """Create the record of one control period.

        Args:
            state: Telescope state received in this period.
            epl: Estimated EPL (in m; feed).
            epl_cal: Estimated EPL at calibration (in m; feed).
                Only its time is stored.
            subref: Result of the controller in this period. Only u
                (the offsets to be sent) is stored: when the controller
                holds its output, the rest of it is from an earlier
                period, so the EPL is taken from epl instead.
            sent: Whether the offsets were sent to COSMOS.
            feed_order: Feed names in the order of the controller's
                arrays (Converter.feed_order).


        """
        # feed names may be in either case (e.g. "c" or "C")
        epls = {
            str(feed).lower(): float(epl.sel(feed=feed))
            for feed in epl.feed.values.tolist()
        }


        w = by_feed(subref.w, feed_order)
        q = by_feed(subref.q, feed_order)
        r = by_feed(subref.r, feed_order)

        return cls(
            time=get_time(epl),
            cosmos_time=state.time,
            cal_time=get_time(epl_cal),
            epl_c=epls.get("c"),
            epl_t=epls.get("t"),
            epl_r=epls.get("r"),
            epl_b=epls.get("b"),
            epl_l=epls.get("l"),
            dx=float(subref.u[0]),
            dz=float(subref.u[1]),
            sent=sent,
            epl_offset_c=w.get("c"),
            epl_offset_t=w.get("t"),
            epl_offset_r=w.get("r"),
            epl_offset_b=w.get("b"),
            epl_offset_l=w.get("l"),
            q_c=q.get("c"),
            q_t=q.get("t"),
            q_r=q.get("r"),
            q_b=q.get("b"),
            q_l=q.get("l"),
            # subref.z is ordered as Z00, Z1-1, Z11, Z20
            z_1=float(subref.z[0]),
            z_2=float(subref.z[2]),
            z_3=float(subref.z[1]),
            z_4=float(subref.z[3]),
            r_c=r.get("c"),
            r_t=r.get("t"),
            r_r=r.get("r"),
            r_b=r.get("b"),
            r_l=r.get("l"),
            xi_x=float(subref.xi[0]),
            xi_z=float(subref.xi[1]),
            d_x=float(subref.d[0]),
            d_z=float(subref.d[1]),
            v_x=float(subref.v[0]),
            v_z=float(subref.v[1]),
            p_x=float(subref.p[0]),
            p_z=float(subref.p[1]),
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
    controller: str
    time_constant_dx: float  # s
    time_constant_dz: float  # s
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
            controller=str(args["controller"]),
            time_constant_dx=float(args["time_constant_dX"]),
            time_constant_dz=float(args["time_constant_dZ"]),
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

def by_feed(values: Any, feed_order: list[str], /) -> dict[str, float]:
    """Map values ordered as feed_order to lower-case feed names."""
    return {str(feed).lower(): float(value) for feed, value in zip(feed_order, values)}


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