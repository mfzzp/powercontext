# Ticket status filter specification version 2

S1: Add status filtering to filter_tickets in ticket_app.py. A non-None status selects matching tickets.
S2: Superseding version 1: normalize BOTH the supplied status and each ticket's stored status with strip().casefold() before comparing. Unknown or blank status returns an empty list.
S3: Apply status filtering before limit; retain input order.
S4: Input tickets always have a string status. No enumeration or ticket ID may be hardcoded.
R1: status=None keeps all tickets and returns a new list in input order.
R2: limit=None is unlimited; limit=0 returns an empty list.
R3: Invalid limits (negative, boolean, non-integer) raise ValueError even with no matching tickets.
R4: Do not mutate the input list or its dictionaries.
R5: Preserve the existing function name, keyword-only signature and behavior outside this feature.
Validation: tests/test_status.py covers the target feature; tests/test_regression.py protects compatibility.
