"""Shared errors raised by the service layers."""


class PermissionDeniedError(Exception):
    """The acting user is not allowed to do this."""


class NotFoundError(Exception):
    """A referenced task or user does not exist."""


class InvalidTransitionError(Exception):
    """The requested status change is not allowed from the task's current status."""
