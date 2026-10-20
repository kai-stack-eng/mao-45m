__all__ = ["control"]


# standard library
from collections import deque
from collections.abc import Sequence
from contextlib import nullcontext
from logging import getLogger
from os import PathLike
from pathlib import Path
from time import sleep
from warnings import catch_warnings, simplefilter

# dependencies
import numpy as np
import xarray as xr
from ndtools import Range
from tqdm import tqdm
from .convert import (
    get_converter as get_subref_converter,
    get_epl_offsets,
    CONTROLLER,
    ControllerState,
)
from .records import Control, Run
from ..cosmos import get_cosmos
from ..database import get_database
from ..epl.convert import get_aggregated, get_converter as get_epl_converter
from ..vdif import FRAMES_PER_SAMPLE
from ..vdif.convert import get_samples_faster as get_samples
from ..vdif.receive import get_frames
from ..utils import log, take, to_datetime, to_timedelta

# constants
LOGGER = getLogger(__name__)
SECOND = np.timedelta64(1, "s")
TIME_UNITS = "microseconds since 2000-01-01T00:00:00"


def control(
    *,
    # options for the feed information
    feed_model: PathLike[str] | str,
    feed_origin: np.datetime64 | str,
    feed_pattern: Sequence[str] | str,
    # options for the EPL estimates
    cal_interval: np.timedelta64 | str | float = "10 s",
    freq_binning: int = 8,
    freq_range: tuple[float, float] = (19.5e9, 22.5e9),  # Hz
    integ_per_sample: np.timedelta64 | str | float = "0.01 s",
    integ_per_epl: np.timedelta64 | str | float = "0.5 s",
    # options for the subref control
    dry_run: bool = True,
    control_duration: np.timedelta64 | str | float = "1 d",
    control_origin: np.datetime64 | str = "Now UTC",
    control_period: np.timedelta64 | str | float = "0.5 s",
    epl_interval_tolerance: float = 0.1,
    epl_offset_schedule: PathLike[str] | str | None = None,
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
    # options for network connection
    cosmos_host: str = "127.0.0.1",
    cosmos_port: int = 11111,
    cosmos_safe: bool = True,
    vdif_group: str = "239.0.0.1",
    vdif_port: int = 22222,
    # option for display
    status: bool = True,
    # options for data saving
    aggregated_data: PathLike[str] | str | None = None,
    aggregated_data_max: int | None = None,
    epl_data: PathLike[str] | str | None = None,
    epl_data_max: int | None = None,
    subref_data: PathLike[str] | str | None = None,
    subref_data_max: int | None = None,
    database: bool = False,
    # options for logging
    log_file: PathLike[str] | str | None = None,
    log_file_level: int | str = "INFO",
    log_stderr: bool = True,
    log_stderr_level: int | str = "WARNING",
) -> None:
    """Control the subreflector of the Nobeyama 45m telescope by MAO."""
    params = dict(locals())

    with log(
        file=log_file,
        file_level=log_file_level,
        stderr=log_stderr,
        stderr_level=log_stderr_level,
    ):
        for key, val in params.items():
            LOGGER.debug(f"{key}: {val!r}")

        # define the frame size for each EPL estimate
        dt_control = to_timedelta(control_duration)
        dt_epl = to_timedelta(integ_per_epl)
        dt_sample = to_timedelta(integ_per_sample)
        epl_size = int(dt_control / dt_epl)
        frame_size = FRAMES_PER_SAMPLE * int(dt_epl / dt_sample)

        # create the EPL and subref converters
        get_epl = get_epl_converter(cal_interval)
        get_subref = get_subref_converter(
            control_period=control_period,
            epl_interval_tolerance=epl_interval_tolerance,
            feed_model=feed_model,
            proportional_gain_dX=proportional_gain_dX,
            proportional_gain_dZ=proportional_gain_dZ,
            integral_gain_dX=integral_gain_dX,
            integral_gain_dZ=integral_gain_dZ,
            anti_windup_gain_dX=anti_windup_gain_dX,
            anti_windup_gain_dZ=anti_windup_gain_dZ,
            time_constant_dX=time_constant_dX,
            time_constant_dZ=time_constant_dZ,
            range_dX=range_dX,
            range_dZ=range_dZ,
            range_ddX=range_ddX,
            range_ddZ=range_ddZ,
            controller=controller,
        )

        # create the EPL offset schedule
        if epl_offset_schedule is not None:
            epl_offsets = get_epl_offsets(epl_offset_schedule)
        else:
            epl_offsets = None

        with (
            tqdm(disable=not status, unit="EPL") as bar,
            get_cosmos(host=cosmos_host, port=cosmos_port, safe=cosmos_safe) as cosmos,
            get_frames(frame_size * 2, group=vdif_group, port=vdif_port) as frames,
            get_database() if database else nullcontext() as db,
        ):
            # wait until enough frames are buffered
            while len(frames.get(frame_size)) != frame_size:
                sleep(dt_epl / SECOND)

            epls = deque(maxlen=epl_data_max)
            aggs = deque(maxlen=aggregated_data_max)
            subrefs = deque(maxlen=subref_data_max)

            # start the subref control
            LOGGER.info("Subreflector control started.")
            control_origin = to_datetime(control_origin)
            # record the start of this run (optional)
            if db is not None:
                db.start_run(Run.from_args(params | {"control_origin": control_origin}))

            try:
                for _ in range(epl_size):
                    with take(dt_epl / SECOND):
                        # get the current telescope state
                        state = cosmos.receive_state()

                        # get the current VDIF samples (time x chan)
                        frames_ = frames.get(frame_size + FRAMES_PER_SAMPLE)
                        samples = get_samples(frames_)

                        # get the aggregated data (feed x freq)
                        aggregated = get_aggregated(
                            samples,
                            elevation=state.elevation,
                            feed_pattern=feed_pattern,
                            feed_origin=feed_origin,
                            freq_binning=freq_binning,
                            freq_range=Range(*freq_range),
                        )

                        # estimate the EPL (in m; feed)
                        epl, epl_cal = get_epl(aggregated)

                        # get the current EPL offset (in m; feed)
                        if epl_offsets is not None:
                            epl_offset = epl_offsets.reindex(
                                time=[to_datetime("Now UTC") - control_origin],
                                method="ffill",
                            )[0]
                        else:
                            epl_offset = None

                        # estimate the current subref parameters
                        subref = get_subref(
                            epl,
                            epl_cal,
                            epl_offset=epl_offset,
                            drive_amount_dX=state.dX,
                            drive_amount_dZ=state.dZ,
                        )

                        # send the subref control to COSMOS
                        # send the subref control to COSMOS, but never a NaN
                        # (e.g. D-PAWPI without the subref position from COSMOS)
                        sent = not dry_run and bool(np.isfinite(subref.u).all())

                        if sent:
                            cosmos.send_subref(
                                dX=subref.u[0],
                                dZ=subref.u[1],
                            )
                        elif not dry_run:
                            LOGGER.warning(f"Subref control not sent: {subref.u}")

                        # write the state, EPL and subref to the database (optional)
                        if db is not None:
                            db.write(
                                state=state,
                                control=Control.from_cycle(
                                    state=state,
                                    epl=epl,
                                    epl_cal=epl_cal,
                                    subref=subref,
                                    sent=sent,
                                ),
                            )
                        # append aggregated, EPL, subref data (optional)
                        if aggregated_data is not None:
                            aggs.append(aggregated)

                        if epl_data is not None:
                            epls.append(epl)

                        if subref_data is not None:
                            subrefs.append(get_subref_dataarray(subref))

                        # update the progress bar
                        bar.update(1)
            except KeyboardInterrupt:
                LOGGER.warning("Control interrupted by user.")
            finally:
                # save aggregated data (optional)
                if aggregated_data is not None:
                    save_dataarray(xr.concat(aggs, dim="time"), aggregated_data)

                # save EPL data (optional)
                if epl_data is not None:
                    save_dataarray(xr.concat(epls, dim="time"), epl_data)

                # save subref data (optional)
                if subref_data is not None:
                    save_dataarray(xr.concat(subrefs, dim="time"), subref_data)

                # finish the subref control
                LOGGER.info("Subreflector control finished.")


def save_dataarray(da: xr.DataArray, zarr: PathLike[str] | str, /) -> Path:
    """Save (append) the given DataArray to the given Zarr file."""
    encoding = {"time": {"units": TIME_UNITS}}

    with catch_warnings():
        simplefilter("ignore", category=UserWarning)

        if Path(zarr).exists():
            da.to_zarr(zarr, mode="a", append_dim="time")
        else:
            da.to_zarr(zarr, mode="w", encoding=encoding)

    return Path(zarr).expanduser().resolve()


def get_subref_dataarray(subref: ControllerState, /) -> xr.DataArray:
    """Convert the controller result to the subref data saved to Zarr.

    The saved data keep the form they had before the controllers returned
    a ControllerState: the offsets to be sent (in m; drive) with a time.

    """
    return xr.DataArray(
        subref.u,
        dims="drive",
        coords={"drive": ["X", "Z"], "time": subref.time},
    )
