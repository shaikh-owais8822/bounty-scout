import io
import unittest
from contextlib import redirect_stdout

import bounty_scout


class ParsingTests(unittest.TestCase):
    def test_extract_items_from_items_key(self):
        items, meta = bounty_scout._extract_items({"count": 1, "items": [{"title": "Test"}]})
        self.assertEqual(len(items), 1)
        self.assertEqual(meta["count"], 1)

    def test_extract_items_from_list(self):
        items, _ = bounty_scout._extract_items([{"title": "Test"}])
        self.assertEqual(items[0]["title"], "Test")

    def test_print_bounties(self):
        output = io.StringIO()
        with redirect_stdout(output):
            bounty_scout.print_bounties({"count": 1, "items": [{"title": "Test bounty", "source": "demo", "est_usd": 2, "url": "https://example.com"}]})
        text = output.getvalue()
        self.assertIn("Test bounty", text)
        self.assertIn("https://example.com", text)
        self.assertIn("2 USD equiv.", text)

    def test_csv(self):
        self.assertEqual(bounty_scout.parse_csv("kyc, stake,,bond"), ["kyc", "stake", "bond"])


if __name__ == "__main__":
    unittest.main()
