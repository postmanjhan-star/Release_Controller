class GiteaError(Exception):
    """Base error for safe Gitea integration failures."""

    error_code = "GITEA_UNAVAILABLE"


class GiteaConnectionError(GiteaError):
    """Gitea could not be reached or returned a server error."""

    error_code = "GITEA_UNAVAILABLE"


class GiteaAuthenticationError(GiteaError):
    """Gitea rejected the configured API token."""

    error_code = "GITEA_AUTH_FAILED"


class GiteaNotFoundError(GiteaError):
    """The requested Gitea resource does not exist."""

    error_code = "GITEA_REPO_NOT_FOUND"


class GiteaConflictError(GiteaError):
    """Gitea rejected a conflicting create operation."""

    error_code = "RELEASE_ALREADY_EXISTS"


class GiteaReleaseError(GiteaError):
    """Gitea could not create a release."""

    error_code = "RELEASE_CREATE_FAILED"


class GiteaTimeoutError(GiteaError):
    """Gitea did not respond within the configured timeout."""

    error_code = "GITEA_TIMEOUT"


class GiteaUnexpectedResponseError(GiteaError):
    """Gitea returned a response that is not a passing health report."""

    error_code = "RELEASE_CREATE_FAILED"
