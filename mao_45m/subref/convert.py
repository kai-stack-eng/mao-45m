__all__ = [
    "Converter",
    "get_anti_windup_gain",
    "get_converter",
    "get_epl_offsets",
    "get_feed_order",
    "get_homologous_epl",
    "get_integral_gain",
    "get_measurement_matrix",
    "get_proportional_gain",
    "get_projection_matrix",
    "get_time_constant",
    "get_zernike_matrix",
    "interp_homologous_epl",
    "saturate_subref_control",
]


# standard library
from dataclasses import dataclass, replace
from functools import cached_property
from logging import getLogger
from os import PathLike
from typing import get_args, Literal

# dependencies
import numpy as np
import pandas as pd
import xarray as xr
from poppy.zernike import zernike
from ..cosmos import ABSMAX_DX, ABSMAX_DZ
from ..utils import to_timedelta

# constants
LOGGER = getLogger(__name__)
NRO45M_DIAMETER = 45.0  # m
SECOND = np.timedelta64(1, "s")
CONTROLLER = Literal["AWPI", "P-AWPI", "M-PAWPI", "D-PAWPI"]


@dataclass
class ControllerState:
    """State of the subreflector controller.

    Args:
        epl: EPL (feed; in m), ordered as b (bottom), c (center), l (left),
            r (right), t (top).
        time: Time of the EPL.
        epl_cal: EPL at calibration (feed; in m), ordered as b, c, l, r, t.
        cal_time: Time of the EPL at calibration.
        w: Artificial disturbance (feed; in m), ordered as b, c, l, r, t.
        q: Estimated residual (q; feed; in m), ordered as b, c, l, r, t.
        z: Estimated Zernike expansion coefficient (z; modes; in m) ordered as
            Z00, Z1-1, Z11, Z20.
        r: Projected estimated residual (r; feed; in m), ordered as b, c, l, r, t.
        xi: Estimated actuation coefficient (xi; drive; in m), ordered as X, Z.
        u: Drive command (u; drive; in m), ordered as X, Z.
        p: Drive amount, measured for D-PAWPI or estimated for M-PAWPI
            (p; drive; in m), ordered as X, Z.
        d: Estimated suppressible disturbance source (d; drive; in m), ordered as X, Z.
        v: Integrator variable (v; drive; in m s), ordered as X, Z.

    """

    epl: np.ndarray
    time: np.datetime64
    epl_cal: np.ndarray
    cal_time: np.datetime64
    w: np.ndarray
    q: np.ndarray
    z: np.ndarray
    r: np.ndarray
    xi: np.ndarray
    u: np.ndarray
    p: np.ndarray
    d: np.ndarray
    v: np.ndarray


@dataclass
class Converter:
    """EPL-to-subref control converter for the Nobeyama 45m telescope.

    Args:
        G: Homologous EPL (G; feed x elevation; in m).
        G_elevation: Elevations of the homologous EPL (elevation; in deg).
        M: Measurement matrix (M; feed x drive).
        P: Projection matrix (P; feed x feed).
        Z: Zernike polynomial matrix (Z; feed x modes).
        K_I: Integral gain (K_I; drive).
        K_P: Proportional gain (K_P; drive).
        K_a: Anti-windup gain (K_a; drive).
        T_s: Time constant (T_s; drive; in s).
        feed_order: Feed order sorted alphabetically (feed).
        control_period: Control period (float in s or string with units).
        epl_interval_tolerance: Acceptable fraction of EPL time interval
            relative to the control period (0.1 means +/- 10% allowance).
        range_dX: Absolute range for dX (in m).
        range_dZ: Absolute range for dZ (in m).
        range_ddX: Absolute range for ddX (in m).
        range_ddZ: Absolute range for ddZ (in m).
        controller: Type of controller to use. Options are "AWPI", "P-AWPI",
            "M-PAWPI", and "D-PAWPI".
        last: Last estimated subreflector parameters.

    """

    G: np.ndarray
    G_elevation: np.ndarray
    M: np.ndarray
    P: np.ndarray
    Z: np.ndarray
    K_I: np.ndarray
    K_P: np.ndarray
    K_a: np.ndarray
    T_s: np.ndarray
    feed_order: list[str]
    control_period: np.timedelta64 | str | float = "0.5 s"
    epl_interval_tolerance: float = 0.1
    range_dX: tuple[float, float] = (-0.038, 0.038)  # m
    range_dZ: tuple[float, float] = (-0.019, 0.019)  # m
    range_ddX: tuple[float, float] = (0.00005, 0.000375)  # m
    range_ddZ: tuple[float, float] = (0.00005, 0.000300)  # m
    controller: CONTROLLER = "AWPI"
    last: ControllerState | None = None

    def __post_init__(self):
        if self.controller not in get_args(CONTROLLER):
            raise ValueError(f"{self.controller} is not in {get_args(CONTROLLER)}")

    @cached_property
    def inv_PMTPM_PMT(self) -> np.ndarray:
        """Pre-calculated (PM^T PM)^-1 PM^T (drive x feed)."""
        PM = self.P @ self.M
        return np.linalg.inv(PM.T @ PM) @ PM.T

    @cached_property
    def inv_ZTZ_ZT(self) -> np.ndarray:
        """Pre-calculated (Z^T Z)^-1 Z^T (modes x feed)."""
        return np.linalg.inv(self.Z.T @ self.Z) @ self.Z.T

    def __call__(
        self,
        epl: xr.DataArray,
        epl_cal: xr.DataArray,
        /,
        *,
        epl_offset: xr.DataArray | None,
        drive_amount_dX: float,
        drive_amount_dZ: float,
    ) -> ControllerState:
        """Convert EPL to subreflector control (u; drive; in m).

        Args:
            epl: EPL to be converted (feed; in m)
                with the telescope state information at that time.
            epl_cal: EPL at calibration (feed; in m; must be zero)
                with the telescope state information at that time.
            epl_offset: EPL offset to be added to the EPL (feed; in m).
            drive_amount_dX: Drive amount in the X direction (drive; in m).
            drive_amount_dZ: Drive amount in the Z direction (drive; in m).

        Returns:
            Estimated subreflector control.

        """

        tc = float(to_timedelta(self.control_period) / SECOND)

        epl_np = select_feeds(epl, self.feed_order).to_numpy()
        epl_cal_np = select_feeds(epl_cal, self.feed_order).to_numpy()

        q = (
            epl_np
            - epl_cal_np
            - interp_homologous_epl(self.G, self.G_elevation, float(epl.elevation))
            + interp_homologous_epl(self.G, self.G_elevation, float(epl_cal.elevation))
        )

        LOGGER.debug(
            "Raw".ljust(10)
            + ", ".join(
                f"EPL[{i}]={1e3 * epl_np[i]:+.3f}mm"
                for i in range(len(self.feed_order))
            )
        )
        LOGGER.debug(
            "Corrected".ljust(10)
            + ", ".join(f"EPL[{i}]={1e3 * q[i]:+.3f}mm" for i in range(len(q)))
        )

        if epl_offset is not None:
            w = select_feeds(epl_offset, self.feed_order).drop_vars("time").to_numpy()
            q = q + w

            LOGGER.debug(
                "Offset".ljust(10)
                + ", ".join(f"EPL[{i}]={1e3 * w[i]:+.3f}mm" for i in range(len(w)))
            )

        else:
            w = np.full(q.shape, np.nan)

        z = self.inv_ZTZ_ZT @ q

        r = self.P @ q

        xi = self.inv_PMTPM_PMT @ r

        if self.last is None:
            u_tmp = -self.K_P * xi

            if self.controller == "D-PAWPI":
                p = np.array([drive_amount_dX, drive_amount_dZ])
                d = xi - p
            elif self.controller == "M-PAWPI":
                d = xi

            else:
                d = np.zeros(u_tmp.shape)
                p = np.full(u_tmp.shape, np.nan)

            u = saturate_subref_control(
                saturate_subref_control(u_tmp, self.range_dX, self.range_dZ) - d,
                self.range_dX,
                self.range_dZ,
            )

            if (self.K_I == 0).all():
                v = np.zeros(u_tmp.shape)
            else:
                v = tc * (
                    xi
                    + self.K_a
                    * (
                        u_tmp
                        - saturate_subref_control(u_tmp, self.range_dX, self.range_dZ)
                    )
                )

            if self.controller == "M-PAWPI":
                p = tc / self.T_s * u

            current = ControllerState(
                epl=epl_np,
                time=epl.time.data,
                epl_cal=epl_cal_np,
                cal_time=epl_cal.time.data,
                w=w,
                q=q,
                z=z,
                r=r,
                xi=xi,
                u=u,
                p=p,
                d=d,
                v=v,
            )

            if np.isnan(current.u).any():
                LOGGER.warning(f"u={current.u} contains NaN.")
                return self.on_failure(current)

            if abs(dX := current.u[0]) > ABSMAX_DX:
                LOGGER.warning(f"{dX=} is out of range (|dX| <= {ABSMAX_DX}).")
                return self.on_failure(current)

            if abs(dZ := current.u[1]) > ABSMAX_DZ:
                LOGGER.warning(f"{dZ=} is out of range (|dZ| <= {ABSMAX_DZ}).")
                return self.on_failure(current)

            return self.on_success(current)

        u_tmp = -self.K_I * self.last.v - self.K_P * xi

        if self.controller == "D-PAWPI":
            p = np.array([drive_amount_dX, drive_amount_dZ])
            d = xi - p
        elif self.controller == "M-PAWPI":
            d = xi - self.last.p
        else:
            d = self.last.d
            p = self.last.p

        u = saturate_subref_control(
            saturate_subref_control(u_tmp, self.range_dX, self.range_dZ) - d,
            self.range_dX,
            self.range_dZ,
        )

        if (self.K_I == 0).all():
            v = self.last.v
        else:
            v = self.last.v + tc * (
                xi
                + self.K_a
                * (u_tmp - saturate_subref_control(u_tmp, self.range_dX, self.range_dZ))
            )

        if self.controller == "M-PAWPI":
            p = self.last.p + tc / self.T_s * (-self.last.p + u)

        current = ControllerState(
            epl=epl_np,
            time=epl.time.data,
            epl_cal=epl_cal_np,
            cal_time=epl_cal.time.data,
            w=w,
            q=q,
            z=z,
            r=r,
            xi=xi,
            u=u,
            p=p,
            d=d,
            v=v,
        )

        dt = (current.time - self.last.time) / SECOND

        if np.isnan(current.u).any():
            LOGGER.warning(f"u={current.u} contains NaN.")
            return self.on_failure(current)

        if abs(dX := current.u[0]) > ABSMAX_DX:
            LOGGER.warning(f"{dX=} is out of range (|dX| <= {ABSMAX_DX}).")
            return self.on_failure(current)

        if abs(dZ := current.u[1]) > ABSMAX_DZ:
            LOGGER.warning(f"{dZ=} is out of range (|dZ| <= {ABSMAX_DZ}).")
            return self.on_failure(current)

        if abs(ddX := dX - self.last.u[0]) < self.range_ddX[0]:
            LOGGER.warning(f"{ddX=} is out of range (|ddX| >= {self.range_ddX[0]}).")
            return self.on_failure(current)

        if abs(ddX) > self.range_ddX[1]:
            LOGGER.warning(f"{ddX=} is out of range (|ddX| <= {self.range_ddX[1]}).")
            return self.on_failure(current)

        if abs(ddZ := dZ - self.last.u[1]) < self.range_ddZ[0]:
            LOGGER.warning(f"{ddZ=} is out of range (|ddZ| >= {self.range_ddZ[0]}).")
            return self.on_failure(current)

        if abs(ddZ) > self.range_ddZ[1]:
            LOGGER.warning(f"{ddZ=} is out of range (|ddZ| <= {self.range_ddZ[1]}).")
            return self.on_failure(current)

        if dt < (dt_min := tc * (1 - self.epl_interval_tolerance)):
            LOGGER.warning(f"{dt=} is out of range (dt >= {dt_min}).")
            return self.on_failure(current)

        if dt > (dt_max := tc * (1 + self.epl_interval_tolerance)):
            LOGGER.warning(f"{dt=} is out of range (dt <= {dt_max}).")
            return self.on_failure(current)

        return self.on_success(current)

    def on_success(self, current: ControllerState, /) -> ControllerState:
        """Replace the last subreflector control with current one."""
        LOGGER.debug(
            ", ".join(
                f"u[{i}]={1e3 * current.u[i]:+.3f}mm" for i in range(len(current.u))
            )
        )
        LOGGER.debug(
            ", ".join(
                f"xi[{i}]={1e3 * current.xi[i]:+.3f}mm" for i in range(len(current.xi))
            )
        )
        self.last = current
        return current

    def on_failure(self, current: ControllerState, /) -> ControllerState:
        """Replace the last subreflector control's time with current one."""
        if self.last is None:
            v_init = np.zeros_like(current.v)
            u_init = np.zeros_like(current.u)
            if self.controller == "M-PAWPI":
                p_init = np.zeros_like(current.p)
            else:
                p_init = current.p
            self.last = replace(current, v=v_init, p=p_init, u=u_init)
        else:
            self.last = replace(self.last, time=current.time)

        return self.last


def get_converter(
    *,
    control_period: np.timedelta64 | str | float = "0.5 s",
    epl_interval_tolerance: float = 0.1,
    feed_model: PathLike[str] | str,
    integral_gain_dX: float = 0.1,
    integral_gain_dZ: float = 0.1,
    proportional_gain_dX: float = 0.1,
    proportional_gain_dZ: float = 0.1,
    anti_windup_gain_dX: float = 10,
    anti_windup_gain_dZ: float = 10,
    time_constant_dX: float = 0.2,
    time_constant_dZ: float = 0.2,
    range_dX: tuple[float, float] = (-0.038, 0.038),  # m
    range_dZ: tuple[float, float] = (-0.019, 0.019),  # m
    range_ddX: tuple[float, float] = (0.00005, 0.000375),  # m
    range_ddZ: tuple[float, float] = (0.00005, 0.000300),  # m
    controller: CONTROLLER = "AWPI",
) -> Converter:
    """Get an EPL-to-subref control converter for the Nobeyama 45m telescope.

    Args:
        control_period: Control period (float in s or string with units).
        epl_interval_tolerance: Acceptable fraction of EPL time interval
            relative to the control period (0.1 means +/- 10% allowance).
        feed_model: Path to the feed model CSV file.
        integral_gain_dX: Integral gain for the estimated dX.
        integral_gain_dZ: Integral gain for the estimated dZ.
        proportional_gain_dX: Proportional gain for the estimated dX.
        proportional_gain_dZ: Proportional gain for the estimated dZ.
        anti_windup_gain_dX: Anti-windup gain for the estimated dX.
        anti_windup_gain_dZ: Anti-windup gain for the estimated dZ.
        time_constant_dX: Time constant of the subreflector drive dX (in s).
        time_constant_dZ: Time constant of the subreflector drive dZ (in s).
        range_dX: Absolute range for dX (in m).
        range_dZ: Absolute range for dZ (in m).
        range_ddX: Absolute range for ddX (in m).
        range_ddZ: Absolute range for ddZ (in m).
        controller: Type of controller to use. Options are "AWPI", "P-AWPI",
            "M-PAWPI", and "D-PAWPI".

    Returns:
        EPL-to-subref control converter.

    """

    G, G_elevation = get_homologous_epl(feed_model)

    return Converter(
        G=G,
        G_elevation=G_elevation,
        M=get_measurement_matrix(feed_model),
        P=get_projection_matrix(feed_model, controller),
        Z=get_zernike_matrix(feed_model),
        K_I=get_integral_gain(integral_gain_dX, integral_gain_dZ),
        K_P=get_proportional_gain(proportional_gain_dX, proportional_gain_dZ),
        K_a=get_anti_windup_gain(anti_windup_gain_dX, anti_windup_gain_dZ),
        T_s=get_time_constant(time_constant_dX, time_constant_dZ),
        feed_order=get_feed_order(feed_model),
        control_period=control_period,
        epl_interval_tolerance=epl_interval_tolerance,
        range_dX=range_dX,
        range_dZ=range_dZ,
        range_ddX=range_ddX,
        range_ddZ=range_ddZ,
        controller=controller,
    )


def get_anti_windup_gain(dX: float, dZ: float, /) -> np.ndarray:
    """Get the anti-windup gain (K_a; drive) from given values."""
    return np.array([dX, dZ])


def get_epl_offsets(epl_offsets: PathLike[str] | str, /) -> xr.DataArray:
    """Get the EPL offsets (time x feed; in m) from given CSV file."""
    df = pd.read_csv(epl_offsets, comment="#", index_col=0, skipinitialspace=True)
    df = df.set_index(pd.to_timedelta(df.index, unit="s"))

    return (
        df.to_xarray()
        .to_dataarray(dim="feed")
        .assign_attrs(units="m")
        .rename("epl_offsets")
        .T
    )


def get_feed_order(feed_model: PathLike[str] | str, /) -> list[str]:
    """Get the feed names in alphabetical order from given feed model.

    Args:
        feed_model: Path to the feed model CSV file.

    Returns:
        Feed names sorted alphabetically (feed).

    """
    df = pd.read_csv(feed_model, comment="#", index_col=0, skipinitialspace=True)

    return sorted(df.index)


def get_homologous_epl(
    feed_model: PathLike[str] | str,
    /,
    *,
    elevation_step: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
    """Get the homologous EPL (G; feed x elevation; in m) and its elevations (in deg).

    Args:
        feed_model: Path to the feed model CSV file.
        elevation_step: Elevation step size (in deg) for calculation.

    Returns:
        Homologous EPL (G; feed x elevation; in m).
        Elevations of the homologous EPL (elevation; in deg).

    """
    df = pd.read_csv(feed_model, comment="#", index_col=0, skipinitialspace=True)
    df = df.loc[sorted(df.index)]
    a = df["homologous_EPL_A"].to_numpy()[:, np.newaxis]
    b = df["homologous_EPL_B"].to_numpy()[:, np.newaxis]
    c = df["homologous_EPL_C"].to_numpy()[:, np.newaxis]
    elevation = np.arange(0, 90.0 + elevation_step, elevation_step)

    return a * np.sin(np.deg2rad(elevation - b)) + c, elevation


def get_integral_gain(dX: float, dZ: float, /) -> np.ndarray:
    """Get the integral gain (K_I; drive) from given values."""
    return np.array([dX, dZ])


def get_measurement_matrix(feed_model: PathLike[str] | str, /) -> np.ndarray:
    """Get the measurement matrix (M; feed x drive) from given feed model.

    Args:
        feed_model: Path to the feed model CSV file.

    Returns:
        Measurement matrix (M; feed x drive).

    """
    df = pd.read_csv(feed_model, comment="#", index_col=0, skipinitialspace=True)
    df = df.loc[sorted(df.index)]

    return np.array([df["EPL_over_dX"], df["EPL_over_dZ"]]).T


def get_projection_matrix(
    feed_model: PathLike[str] | str,
    controller: CONTROLLER,
    /,
) -> np.ndarray:
    """Get the projection matrix (P; feed_proj x feed)."""
    df = pd.read_csv(feed_model, comment="#", index_col=0, skipinitialspace=True)
    df = df.loc[sorted(df.index)]
    n_feed = len(df.index)

    if controller == "AWPI":
        return np.eye(n_feed)

    return np.eye(n_feed) - 1 / n_feed * np.ones((n_feed, n_feed))


def get_proportional_gain(dX: float, dZ: float, /) -> np.ndarray:
    """Get the proportional gain (K_P; drive) from given values."""
    return np.array([dX, dZ])


def get_time_constant(dX: float, dZ: float, /) -> np.ndarray:
    """Get the time constant matrix (T_s; drive) from given values."""
    return np.array([dX, dZ])


def get_zernike_matrix(feed_model: PathLike[str] | str, /) -> np.ndarray:
    """Get the Zernike polynomial matrix (Z; feed x modes) from given feed model.

    Args:
        feed_model: Path to the feed model CSV file.

    Returns:
        Zernike polynomial matrix (Z; feed x modes). The column components are sorted
        as Z00, Z1-1, Z11, Z20.

    """
    df = pd.read_csv(feed_model, comment="#", index_col=0, skipinitialspace=True)
    df = df.loc[sorted(df.index)]
    rho = df["position_radius"] / (NRO45M_DIAMETER / 2)
    theta = np.deg2rad(df["position_angle"])

    return np.array(
        [
            zernike(0, 0, rho=rho, theta=theta, noll_normalize=False),
            zernike(1, -1, rho=rho, theta=theta, noll_normalize=False),
            zernike(1, 1, rho=rho, theta=theta, noll_normalize=False),
            zernike(2, 0, rho=rho, theta=theta, noll_normalize=False),
        ]
    ).T


def interp_homologous_epl(
    G: np.ndarray,
    G_elevation: np.ndarray,
    elevation: float,
    /,
) -> np.ndarray:
    """Linearly interpolate the homologous EPL (feed; in m) at given elevation."""
    return np.array([np.interp(elevation, G_elevation, G_feed) for G_feed in G])


def saturate_subref_control(
    u_tmp: np.ndarray, range_dX: tuple[float, float], range_dZ: tuple[float, float]
) -> np.ndarray:
    """Saturation function to limit the subreflector control within the range."""
    u = u_tmp.copy()
    if u[0] < range_dX[0]:
        u[0] = range_dX[0]

    if u[0] > range_dX[1]:
        u[0] = range_dX[1]

    if u[1] < range_dZ[0]:
        u[1] = range_dZ[0]

    if u[1] > range_dZ[1]:
        u[1] = range_dZ[1]

    return u

def select_feeds(da: xr.DataArray, feed_order: list[str], /) -> xr.DataArray:
    """Select the feeds in given order, ignoring the case of the feed names.

    The feed names of the feed model (see get_feed_order) and those of
    the EPL, which follow the feed pattern, may be in either case.

    """
    lower = [str(feed).lower() for feed in da.feed.values]
    return da.assign_coords(feed=lower).sel(
        feed=[str(feed).lower() for feed in feed_order]
    )
