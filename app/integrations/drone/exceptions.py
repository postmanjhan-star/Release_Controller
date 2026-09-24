class DroneError(Exception):
    """Base class for safe, classified Drone integration errors."""

    error_code = "DRONE_UNAVAILABLE"


class DroneConnectionError(DroneError):
    error_code = "DRONE_UNAVAILABLE"


class DroneAuthenticationError(DroneError):
    error_code = "DRONE_AUTH_FAILED"


class DroneNotFoundError(DroneError):
    error_code = "BUILD_NOT_FOUND"


class DronePromoteError(DroneError):
    error_code = "DRONE_PROMOTE_FAILED"


class DroneTimeoutError(DroneError):
    error_code = "DRONE_TIMEOUT"


class DroneUnexpectedResponseError(DroneError):
    error_code = "DRONE_UNAVAILABLE"
