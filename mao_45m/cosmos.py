__all__ = ["Cosmos", "State", "Subref", "get_cosmos", "get_socket", "receive", "send"]


# standard library
import re
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger
from socket import AF_INET, SOCK_STREAM, socket
from zoneinfo import ZoneInfo

# dependencies
from typing_extensions import Self

# constants
ABSMAX_DX = 0.048  # m
ABSMAX_DZ = 0.024  # m
LOGGER = getLogger(__name__)
FIELD_FORMAT = re.compile(r"(\w+):\s*([0-9.eE+-]+)")
SUBREF_DX_FORMAT = re.compile(r"set\s+x\s+([0-9.+-]+)")
SUBREF_DZ_FORMAT = re.compile(r"set\s+z\s+([0-9.+-]+)")
TIME_FORMAT = "%y%m%d%H%M%S.%f"
TZ = ZoneInfo("Asia/Tokyo")


class Cosmos:
    """COSMOS client for the Nobeyama 45m telescope.

    Args:
        host: IP address of the COSMOS server.
        port: Port number of the COSMOS server.
        safe: Whether to raise an error before sending
            if the subreflector parameters are out of range.

    Example:
        ::

            from mao_45m.cosmos import Cosmos, Subref

            with Cosmos(host="127.0.0.1", port=11111) as cosmos:
                state = cosmos.receive_state()
                subref = cosmos.send_subref(dX=1.0, dZ=2.0)

    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 11111,
        safe: bool = True,
    ) -> None:
        self.sock = get_socket(host=host, port=port)
        self.safe = safe

    def send_subref(
        self,
        *,
        cmd: str = "pushsub",
        dX: float,
        dZ: float,
    ) -> "Subref":
        """Send the subreflector parameters of the Nobeyama 45m telescope.

        Args:
            cmd: Command name or path to send the parameters.
            dX: Offset (in m) from the X cylinder position
                optimized for the gravity deformation correction.
            dZ: Offset (in m) from the Z cylinder positions (Z1 = Z2)
                optimized for the gravity deformation correction.

        Returns:
            Current subreflector parameters received from COSMOS.

        """
        # check if dX and dZ are within the acceptable ranges
        if abs(dX) > ABSMAX_DX:
            if self.safe:
                raise ValueError(f"{dX=} is out of range (|dX| <= {ABSMAX_DX}).")
            else:
                LOGGER.warning(f"{dX=} is out of range (|dX| <= {ABSMAX_DX}).")

        if abs(dZ) > ABSMAX_DZ:
            if self.safe:
                raise ValueError(f"{dZ=} is out of range (|dZ| <= {ABSMAX_DZ}).")
            else:
                LOGGER.warning(f"{dZ=} is out of range (|dZ| <= {ABSMAX_DZ}).")

        # dX after m to mm conversion will be sent
        cmd_dX = f"{cmd} x {1e3 * dX}"
        LOGGER.debug(cmd_dX)
        self.sock.send((cmd_dX + "\n").encode())
        resp_dX = self.sock.recv(1024)

        # dZ after m to mm conversion will be sent
        cmd_dZ = f"{cmd} z {1e3 * dZ}"
        LOGGER.debug(cmd_dZ)
        self.sock.send((cmd_dZ + "\n").encode())
        resp_dZ = self.sock.recv(1024)

        # dX and dZ after mm to m will be stored
        return Subref.from_cosmos(resp_dX, resp_dZ)

    def receive_state(self, *, cmd: str = "pullwte") -> "State":
        """Receive the state of the Nobeyama 45m telescope.

        Args:
            cmd: Command name or path to receive the state.

        Returns:
            Current state of the Nobeyama 45m telescope.

        """

        LOGGER.debug(cmd)
        self.sock.send((cmd + "\n").encode())
        resp = self.sock.recv(1024)

        return State.from_cosmos(resp)

    def __enter__(self) -> Self:
        """Enter the context manager."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Exit the context manager."""
        self.sock.close()


@dataclass(frozen=True)
class State:
    """Current state of the Nobeyama 45m telescope.

    Args:
        wind_speed: Wind speed (in m s^-1).
        temp: Ambient temperature (in deg C).
        elevation: Elevation angle of the telescope (in deg).
        time: Timestamp of the state (timezone-aware).

    """

    wind_speed: float
    wind_direction: float
    temperature: float
    elevation: float
    azimuth: float
    time: datetime

    @classmethod
    def from_cosmos(cls, resp: bytes, /) -> Self:
        """Parse the current state from the COSMOS response."""
        fields = dict(FIELD_FORMAT.findall(resp.decode()))

        try:
            return cls(
                wind_speed=float(fields["wind"]),
                wind_direction=float(fields["dir"]),
                temperature=float(fields["tmp"]),
                elevation=float(fields["el"]),
                azimuth=float(fields["az"]),
                time=datetime.strptime(fields["time"], TIME_FORMAT).replace(tzinfo=TZ),
            )
        except KeyError as error:
            raise ValueError(
                f"Could not parse the COSMOS response: {resp!r}"
            ) from error


@dataclass(frozen=True)
class Subref:
    """Subreflector parameters of the Nobeyama 45m telescope.

    Args:
        dX: Offset (in m) from the X cylinder position
            optimized for the gravity deformation correction.
        dZ: Offset (in m) from the Z cylinder positions (Z1 = Z2)
            optimized for the gravity deformation correction.

    """

    dX: float
    dZ: float

    @classmethod
    def from_cosmos(cls, resp_dX: bytes, resp_dZ: bytes, /) -> Self:
        """Parse the subref parameters from the COSMOS response."""
        if (match_dX := SUBREF_DX_FORMAT.search(resp_dX.decode())) is None:
            raise ValueError("Could not parse the COSMOS response of dX.")

        if (match_dZ := SUBREF_DZ_FORMAT.search(resp_dZ.decode())) is None:
            raise ValueError("Could not parse the COSMOS response of dZ.")

        # dX and dZ after mm to m will be stored
        return cls(dX=1e-3 * float(match_dX[1]), dZ=1e-3 * float(match_dZ[1]))


def get_cosmos(
    *,
    host: str = "127.0.0.1",
    port: int = 11111,
    safe: bool = True,
) -> Cosmos:
    """Get a COSMOS client for the Nobeyama 45m telescope.

    Args:
        host: IP address of the COSMOS server.
        port: Port number of the COSMOS server.
        safe: Whether to raise an error before sending
            if the subreflector parameters are out of range.

    Returns:
        COSMOS client for the Nobeyama 45m telescope.

    """
    return Cosmos(host=host, port=port, safe=safe)


def receive(
    *,
    cmd: str = "pullwte",
    host: str = "127.0.0.1",
    port: int = 11111,
) -> State:
    """Receive the current state of the Nobeyama 45m telescope.

    Args:
        cmd: Command name or path to receive the state.
        host: IP address of the COSMOS server.
        port: Port number of the COSMOS server.

    Returns:
        Current state of the Nobeyama 45m telescope.

    """
    with Cosmos(host=host, port=port) as cosmos:
        return cosmos.receive_state(cmd=cmd)


def send(
    *,
    cmd: str = "pushsub",
    dX: float,
    dZ: float,
    host: str = "127.0.0.1",
    port: int = 11111,
    safe: bool = True,
) -> Subref:
    """Send the subreflector parameters of the Nobeyama 45m telescope.

    Args:
        cmd: Command name or path to send the parameters.
        dX: Offset (in m) from the X cylinder position
            optimized for the gravity deformation correction.
        dZ: Offset (in m) from the Z cylinder positions (Z1 = Z2)
            optimized for the gravity deformation correction.
        host: IP address of the COSMOS server.
        port: Port number of the COSMOS server.
        safe: Whether to raise an error before sending
            if the subreflector parameters are out of range.

    Returns:
        Current subreflector parameters received from COSMOS.

    """
    with Cosmos(host=host, port=port, safe=safe) as cosmos:
        return cosmos.send_subref(cmd=cmd, dX=dX, dZ=dZ)


def get_socket(*, host: str = "127.0.0.1", port: int = 11111) -> socket:
    """Get a socket object for TCP connection."""
    sock = socket(AF_INET, SOCK_STREAM)
    sock.connect((host, port))
    return sock
