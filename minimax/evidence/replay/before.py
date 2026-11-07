"""Small ticket listing service. Status filtering is the requested missing feature."""


def filter_tickets(tickets, *, status=None, limit=None):
    """Return tickets in input order, optionally limited. Never mutate input."""
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be a non-negative integer or None")
    result = list(tickets)
    return result if limit is None else result[:limit]
