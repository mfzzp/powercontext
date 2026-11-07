"""Target tests for ticket status filter specification S1-S4; fixed before model call."""
import unittest

from ticket_app import filter_tickets


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tickets = [
            {"id": 17, "status": "closed", "title": "Archived"},
            {"id": 4, "status": "open", "title": "New"},
            {"id": 91, "status": "open", "title": "Waiting"},
        ]

    def test_exact_status(self):
        self.assertEqual([4, 91], [t["id"] for t in filter_tickets(self.tickets, status="open")])

    def test_case_insensitive(self):
        self.assertEqual([17], [t["id"] for t in filter_tickets(self.tickets, status="CLOSED")])

    def test_trim_status(self):
        self.assertEqual([4, 91], [t["id"] for t in filter_tickets(self.tickets, status=" open ")])

    def test_filter_before_limit(self):
        self.assertEqual([4], [t["id"] for t in filter_tickets(self.tickets, status="open", limit=1)])

    def test_unknown_status(self):
        self.assertEqual([], filter_tickets(self.tickets, status="waiting-for-review"))

    def test_blank_status(self):
        self.assertEqual([], filter_tickets(self.tickets, status="  "))

    def test_normalize_stored_status(self):
        data = [{"id": 56, "status": " Open "}, {"id": 29, "status": "closed"}]
        self.assertEqual([data[0]], filter_tickets(data, status="open"))
