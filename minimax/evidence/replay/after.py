"""Small ticket listing service. Status filtering is the requested missing feature."""


def filter_tickets(tickets, *, status=None, limit=None):
    """Return tickets in input order, optionally filtered by status and limited.

    Never mutate the input list or its ticket dictionaries.
    """
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be a non-negative integer or None")

    if status is None:
        result = list(tickets)
    else:
        normalized_status = status.strip().casefold()
        if not normalized_status:
            return []
        result = [
            ticket
            for ticket in tickets
            if ticket["status"].strip().casefold() == normalized_status
        ]

    return result if limit is None else result[:limit]
