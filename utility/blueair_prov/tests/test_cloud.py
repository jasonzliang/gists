import base64
import re
import unittest

from blueair_prov import constants as C
from blueair_prov.cloud import (
    android_base64_default,
    classify_device_event,
    generate_random_text,
    generate_secure_random,
)


class TestGenerators(unittest.TestCase):
    def test_random_text(self):
        t = generate_random_text()
        self.assertEqual(len(t), 128)
        self.assertRegex(t, r"^[A-Za-z0-9]{128}$")
        self.assertNotEqual(t, generate_random_text())

    def test_android_base64_default_format(self):
        s = generate_secure_random()
        self.assertEqual(len(s), 90)  # 76 + \n + 12 + \n
        lines = s.split("\n")
        self.assertEqual(lines[-1], "")
        self.assertEqual(len(lines[0]), 76)
        self.assertEqual(len(lines[1]), 12)
        raw = base64.b64decode(s.replace("\n", ""))
        self.assertEqual(len(raw), 64)

    def test_android_base64_known(self):
        self.assertEqual(android_base64_default(b"hello"), "aGVsbG8=\n")
        self.assertEqual(android_base64_default(bytes(57)), "A" * 76 + "\n")
        self.assertEqual(android_base64_default(bytes(58)), "A" * 76 + "\n" + "AA==\n")


class TestRegionTable(unittest.TestCase):
    def test_matches_blueair_api_constants(self):
        from blueair_api.const import AWS_APIKEYS, AWS_MQTT_BROKERS

        for key, vals in C.BLUEAIR_CLOUD_REGIONS.items():
            ak = AWS_APIKEYS[key]
            self.assertEqual(vals["api_url"], f"https://{ak['restApiId']}.execute-api.{ak['awsRegion']}/prod", key)
            self.assertEqual(vals["auth_url"], vals["api_url"] + "/c/authenticate", key)
            self.assertEqual(vals["broker_url"], AWS_MQTT_BROKERS[key], key)
            self.assertTrue(ak["awsRegion"].startswith(vals["region"] + "."), key)

    def test_no_local_rust_example_values(self):
        for vals in C.BLUEAIR_CLOUD_REGIONS.values():
            for v in vals.values():
                self.assertNotIn("192.168.", v)


class TestEventClassification(unittest.TestCase):
    def test_success(self):
        v = classify_device_event({"et": "DeviceBound", "ec": 0, "o": "abc-uuid"})
        self.assertEqual((v.kind, v.device_uuid), ("success", "abc-uuid"))

    def test_wait_states(self):
        for et in ("LinkConnect", "ObtainingIPAddress", "BrokerConnected", "RegisterDevice"):
            self.assertEqual(classify_device_event({"et": et, "ec": 0}).kind, "wait")
        self.assertEqual(classify_device_event({"et": "BrokerConnecting", "ec": -5}).kind, "wait")

    def test_errors(self):
        self.assertIn("JWT", classify_device_event({"et": "LinkConnect", "ec": -4}).message)
        self.assertIn("password", classify_device_event({"et": "Authenticating", "ec": -3}).message)
        self.assertIn("password", classify_device_event({"et": "BrokerConnected", "ec": -8}).message)
        self.assertIn("router", classify_device_event({"et": "ObtainingIPAddress", "ec": -2}).message)
        self.assertIn("internet", classify_device_event({"et": "LinkConnected", "ec": -99}).message)
        self.assertIn("signal", classify_device_event({"et": "SettingPassword", "ec": -1}).message)
        self.assertEqual(classify_device_event({"et": "DeviceBound", "ec": -1}).kind, "error")

    def test_ignore(self):
        self.assertEqual(classify_device_event(None).kind, "ignore")
        self.assertEqual(classify_device_event("str").kind, "ignore")


if __name__ == "__main__":
    unittest.main()
