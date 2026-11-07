"""Ticket listing compatibility tests R1-R5, also relevant to the status filter change."""
import copy
import unittest

from ticket_app import filter_tickets


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.data = [{"id": 12, "status": "open"}, {"id": 7, "status": "closed"}]

    def test_no_filter_order(self):
        result = filter_tickets(self.data)
        self.assertEqual(self.data, result)
        self.assertIsNot(self.data, result)

    def test_limit_without_filter(self):
        self.assertEqual(self.data[:1], filter_tickets(self.data, limit=1))

    def test_limit_zero(self):
        self.assertEqual([], filter_tickets(self.data, limit=0))

    def test_empty_input(self):
        self.assertEqual([], filter_tickets([], status="open"))

    def test_invalid_limit(self):
        for limit in [-1, 1.5, "1", True]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                filter_tickets(self.data, limit=limit)

    def test_no_mutation(self):
        saved = copy.deepcopy(self.data)
        filter_tickets(self.data, status="open", limit=1)
        self.assertEqual(saved, self.data)
