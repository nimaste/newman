"""Every connection says which kinds of device it brings in (the catalog shows them), and only real kinds."""
import os
import unittest

os.environ.setdefault("TOKEN_SECRET", "test-secret")

from newman.integrations import REGISTRY  # noqa: E402
from newman.kinds import KINDS  # noqa: E402


class ProvidesTests(unittest.TestCase):
    def test_every_connection_declares_real_kinds(self):
        for type_, cls in REGISTRY.items():
            if not cls.available:
                continue
            self.assertTrue(cls.provides, f"{type_} doesn't say what it provides")
            unknown = [k for k in cls.provides if k not in KINDS]
            self.assertEqual(unknown, [], f"{type_} lists kinds that don't exist")
            self.assertEqual(cls.catalog_json()["provides"], list(cls.provides))
