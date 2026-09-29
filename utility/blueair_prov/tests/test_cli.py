import os
import unittest
from unittest import mock

from blueair_prov import cli


class TestProvisionGuards(unittest.TestCase):
    """These exit before any BLE/cloud activity."""

    def run_cli(self, argv, env=None):
        with mock.patch.dict(os.environ, env or {}, clear=True):
            return cli.main(argv)

    def test_refuses_without_ssid(self):
        self.assertEqual(self.run_cli(["provision"]), 2)

    def test_refuses_without_password(self):
        self.assertEqual(self.run_cli(["provision"], {"BLUEAIR_WIFI_SSID": "Net"}), 2)

    def test_refuses_without_yes(self):
        rc = self.run_cli(["provision"], {"BLUEAIR_WIFI_SSID": "Net", "BLUEAIR_WIFI_PASS": "pw"})
        self.assertEqual(rc, 2)

    def test_refuses_partial_config(self):
        rc = self.run_cli(["provision", "--yes", "--api-url", "https://x"], {"BLUEAIR_WIFI_SSID": "Net", "BLUEAIR_WIFI_PASS": "pw"})
        self.assertEqual(rc, 2)

    def test_cloud_requires_account(self):
        rc = self.run_cli(["provision", "--yes", "--cloud", "--cloud-region", "us"], {"BLUEAIR_WIFI_SSID": "Net", "BLUEAIR_WIFI_PASS": "pw"})
        self.assertEqual(rc, 2)

    def test_resolve_config_default_none(self):
        args = cli.build_parser().parse_args(["provision"])
        self.assertEqual(cli.resolve_config(args), (None, {}))

    def test_resolve_config_cloud_region_generates(self):
        args = cli.build_parser().parse_args(["provision", "--cloud-region", "eu"])
        cfg, gen = cli.resolve_config(args)
        self.assertEqual(cfg["region"], "eu-west-1")
        self.assertEqual(set(gen), {"random_text", "secure_random"})
        self.assertEqual(len(cfg), 6)

    def test_skip_configuration_wins(self):
        args = cli.build_parser().parse_args(["provision", "--cloud-region", "us", "--skip-configuration"])
        self.assertEqual(cli.resolve_config(args), (None, {}))


if __name__ == "__main__":
    unittest.main()
