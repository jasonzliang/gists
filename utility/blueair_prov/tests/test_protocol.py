import unittest

from blueair_prov import constants as C
from blueair_prov.protocol import ProvisioningError, Session, SessionDesync
from blueair_prov.protos import custom_commands_pb2, wifi_constants_pb2, wifi_scan_pb2

from .fake_device import FakeDevice, FakeNetwork

NETS = [FakeNetwork(f"net{i}", bytes([i] * 6), 1 + i, -40 - i, i % 8) for i in range(9)]


class TestSession(unittest.IsolatedAsyncioTestCase):
    async def test_proto_ver_writes_ESP_and_reads_json(self):
        dev = FakeDevice()
        s = Session(dev)
        raw = await s.get_proto_ver()
        self.assertEqual(dev.requests[0], ("proto-ver", b"ESP"))
        self.assertEqual(raw, dev.proto_ver)
        self.assertEqual(s.proto_ver_json["prov"]["ver"], "v1.1")

    async def test_sec1_handshake_no_pop(self):
        dev = FakeDevice()
        s = Session(dev)
        await s.establish()
        self.assertEqual(s.device_random, dev.device_random)
        self.assertEqual(s.cipher.bytes_processed, 64)  # 32 verify out + 32 verify in
        kinds = [r[1].sec1.WhichOneof("payload") for r in dev.requests if r[0] == "prov-session"]
        self.assertEqual(kinds, ["sc0", "sc1"])

    async def test_sec1_handshake_with_pop_both_sides(self):
        dev = FakeDevice(pop="abcd1234")
        await Session(dev, pop="abcd1234").establish()

    async def test_pop_mismatch_fails_verify(self):
        dev = FakeDevice(pop="abcd1234")
        with self.assertRaises(ProvisioningError):
            await Session(dev).establish()  # device rejects sc1 -> CryptoError status

    async def test_device_bad_verify_data_detected(self):
        dev = FakeDevice(fail_session1=True)
        with self.assertRaisesRegex(ProvisioningError, "CryptoError"):
            await Session(dev).establish()

    async def test_sec0(self):
        dev = FakeDevice()
        s = Session(dev, sec=0)
        await s.establish()
        await s.start()
        self.assertTrue(dev.started)

    async def test_start_and_configuration_order(self):
        dev = FakeDevice()
        s = Session(dev)
        await s.establish()
        await s.start()
        cfg = {k: f"val-{k}" for k in C.CONFIG_FIELD_ORDER}
        await s.set_configuration(dict(reversed(list(cfg.items()))))  # order in dict must not matter
        sent = [r[1].config_cmd.WhichOneof("payload") for r in dev.requests if r[0] == "custom-endpoint" and r[1].WhichOneof("payload") == "config_cmd"]
        self.assertEqual(sent, list(C.CONFIG_FIELD_ORDER))
        self.assertEqual(dev.config, cfg)
        self.assertTrue(s.is_configured)

    async def test_wifi_scan_pagination(self):
        dev = FakeDevice(networks=NETS, scan_polls_needed=3)
        s = Session(dev)
        await s.establish()
        res = await s.wifi_scan(poll_interval=0)
        self.assertEqual([r.ssid for r in res], [n.ssid for n in NETS])
        self.assertEqual(res[3].auth, "WPA2_PSK")
        self.assertEqual(res[0].bssid, "000000000000")
        scan_reqs = [r[1] for r in dev.requests if r[0] == "prov-scan"]
        start = scan_reqs[0].cmd_scan_start
        self.assertEqual((start.blocking, start.passive, start.group_channels, start.period_ms), (True, False, 0, 120))
        self.assertEqual(sum(1 for r in scan_reqs if r.WhichOneof("payload") == "cmd_scan_status"), 3)
        pages = [(r.cmd_scan_result.start_index, r.cmd_scan_result.count) for r in scan_reqs if r.WhichOneof("payload") == "cmd_scan_result"]
        self.assertEqual(pages, [(0, 4), (4, 4), (8, 1)])  # WIFI_PACKET_COUNT = 4

    async def test_wifi_scan_never_finishes(self):
        dev = FakeDevice(networks=NETS, scan_polls_needed=99)
        s = Session(dev)
        await s.establish()
        with self.assertRaisesRegex(ProvisioningError, "did not finish"):
            await s.wifi_scan(poll_interval=0)
        self.assertEqual(sum(1 for r in dev.requests if r[0] == "prov-scan" and r[1].WhichOneof("payload") == "cmd_scan_status"), 10)

    async def test_wifi_connect_success(self):
        dev = FakeDevice()
        s = Session(dev)
        await s.establish()
        st = await s.wifi_connect("Home", "pw123456", poll_interval=0)
        self.assertEqual(st.sta_state_name, "Connected")
        self.assertEqual(st.ip4_addr, "192.168.1.50")
        self.assertEqual(dev.wifi, {"ssid": b"Home", "passphrase": b"pw123456"})
        self.assertTrue(dev.applied)

    async def test_wifi_connect_auth_error(self):
        dev = FakeDevice(status_sequence=[(wifi_constants_pb2.ConnectionFailed, wifi_constants_pb2.AuthError, None)])
        s = Session(dev)
        await s.establish()
        with self.assertRaisesRegex(ProvisioningError, "AuthError"):
            await s.wifi_connect("Home", "bad", poll_interval=0)

    async def test_wifi_connect_timeout(self):
        dev = FakeDevice(status_sequence=[(wifi_constants_pb2.Connecting, None, None)])
        s = Session(dev)
        await s.establish()
        with self.assertRaisesRegex(ProvisioningError, "timeout"):
            await s.wifi_connect("Home", "pw", timeout=0.05, poll_interval=0.01)

    async def test_require_configured_guard(self):
        dev = FakeDevice()
        s = Session(dev)
        await s.establish()
        with self.assertRaisesRegex(ProvisioningError, "not configured"):
            await s.wifi_connect("Home", "pw", require_configured=True)

    async def test_events_drain(self):
        evs = [{"et": "LinkConnect", "ec": 0}, {"et": "BrokerConnected", "ec": 0}, {"et": "DeviceBound", "ec": 0, "o": "uuid-1"}]
        dev = FakeDevice(events=evs)
        s = Session(dev)
        await s.establish()
        got = await s.get_all_event()
        self.assertEqual([e.json for e in got[:3]], evs)
        self.assertEqual(got[-1].number_of_events, 0)
        self.assertEqual(len(got), 3)

    async def test_events_safety_cap(self):
        dev = FakeDevice(events=[{"et": "x", "ec": 0}] * 100)
        s = Session(dev)
        await s.establish()
        got = await s.get_all_event(max_events=5)
        self.assertEqual(len(got), 5)

    async def test_empty_read_is_desync(self):
        dev = FakeDevice()
        s = Session(dev)
        await s.establish()

        async def bad_read(ep):
            return b""

        dev.read = bad_read
        with self.assertRaises(SessionDesync):
            await s.start()

    async def test_full_rust_main_flow(self):
        """main.rs: proto-ver -> session -> StartCmd -> 6x ConfigCmd -> wifi -> events."""
        dev = FakeDevice(events=[{"et": "DeviceBound", "ec": 0, "o": "u"}])
        s = Session(dev)
        await s.get_proto_ver()
        await s.establish()
        await s.start()
        await s.set_configuration({k: k for k in C.CONFIG_FIELD_ORDER})
        await s.wifi_connect("S", "P", poll_interval=0)
        evs = await s.get_all_event()
        self.assertEqual(evs[0].json["et"], "DeviceBound")
        order = [r[0] for r in dev.requests]
        self.assertEqual(order[:4], ["proto-ver", "prov-session", "prov-session", "custom-endpoint"])
        self.assertEqual(order.count("custom-endpoint"), 1 + 6 + 1)
        self.assertEqual(order.count("prov-config"), 2 + 2)  # set, apply, 2 status polls


if __name__ == "__main__":
    unittest.main()
