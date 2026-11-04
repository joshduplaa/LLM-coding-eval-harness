"""Exceptions raised by the harness itself (never by the code under test)."""


class HarnessError(Exception):
    """Something went wrong in the harness or its environment, not in the model's code."""


class TaskValidationError(HarnessError):
    """A task's tests don't separate its reference solution from its incorrect ones."""
