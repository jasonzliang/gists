import unittest

from blueair_prov.protos import (
    constants_pb2,
    custom_commands_pb2,
    sec1_pb2,
    session_pb2,
    wifi_config_pb2,
    wifi_constants_pb2,
    wifi_scan_pb2,
)


class TestSessionProtos(unittest.TestCase):
    def test_session_cmd0_wire_bytes(self):
        """Hand-derived canonical encoding (what protobuf-rs also emits):
        10 01            sec_ver = 1
        5a 25            field 11 (sec1), len 37
          a2 01 22       field 20 (sc0), len 34
            0a 20 <32B>  field 1 client_pubkey
        msg=Session_Command0 (0) is a proto3 default and therefore ABSENT."""
        s = session_pb2.SessionData(sec_ver=session_pb2.SecScheme1)
        s.sec1.msg = sec1_pb2.Session_Command0
        s.sec1.sc0.client_pubkey = b"\x01" * 32
        b = s.SerializeToString()
        self.assertEqual(b, bytes.fromhex("10015a25a201220a20") + b"\x01" * 32)
        r = session_pb2.SessionData.FromString(b)
        self.assertEqual(r.WhichOneof("proto"), "sec1")
        self.assertEqual(r.sec1.WhichOneof("payload"), "sc0")
        self.assertEqual(r.sec1.sc0.client_pubkey, b"\x01" * 32)

    def test_session_cmd1_round_trip(self):
        s = session_pb2.SessionData(sec_ver=session_pb2.SecScheme1)
        s.sec1.msg = sec1_pb2.Session_Command1
        s.sec1.sc1.client_verify_data = bytes(range(32))
        b = s.SerializeToString()
        self.assertTrue(b.startswith(bytes.fromhex("1001")))
        r = session_pb2.SessionData.FromString(b)
        self.assertEqual(r.sec1.msg, sec1_pb2.Session_Command1)
        self.assertEqual(r.sec1.WhichOneof("payload"), "sc1")

    def test_resp0_success_status_absent_on_wire(self):
        s = session_pb2.SessionData(sec_ver=session_pb2.SecScheme1)
        s.sec1.msg = sec1_pb2.Session_Response0
        s.sec1.sr0.status = constants_pb2.Success
        s.sec1.sr0.device_pubkey = b"\x02" * 32
        s.sec1.sr0.device_random = b"\x03" * 16
        r = session_pb2.SessionData.FromString(s.SerializeToString())
        self.assertEqual(r.sec1.WhichOneof("payload"), "sr0")
        self.assertEqual(r.sec1.sr0.status, 0)
        self.assertNotIn(b"\x08\x00", s.sec1.sr0.SerializeToString())  # status field not written

    def test_sec0(self):
        s = session_pb2.SessionData(sec_ver=session_pb2.SecScheme0)
        s.sec0.sc.SetInParent()
        b = s.SerializeToString()
        self.assertEqual(b, bytes.fromhex("52 03 a2 01 00".replace(" ", "")))  # field10 len3 { field20 (2-byte tag) len0 }
        self.assertEqual(session_pb2.SessionData.FromString(b).WhichOneof("proto"), "sec0")


class TestCustomCommands(unittest.TestCase):
    def test_start_cmd_wire(self):
        w = custom_commands_pb2.CommandWrapper()
        w.start_cmd.SetInParent()
        self.assertEqual(w.SerializeToString(), bytes.fromhex("0a00"))  # field 1, empty
        r = custom_commands_pb2.CommandWrapper.FromString(b"\x0a\x00")
        self.assertEqual(r.WhichOneof("payload"), "start_cmd")

    def test_config_cmd_each_field(self):
        for i, name in enumerate(["api_url", "auth_url", "broker_url", "region", "random_text", "secure_random"], start=1):
            w = custom_commands_pb2.CommandWrapper()
            setattr(w.config_cmd, name, "v")
            b = w.SerializeToString()
            # outer: field 3 (config_cmd) -> 0x1a len; inner: field i string "v" -> (i<<3|2) 01 76
            self.assertEqual(b, bytes([0x1A, 3, (i << 3) | 2, 1, ord("v")]))
            r = custom_commands_pb2.CommandWrapper.FromString(b)
            self.assertEqual(r.config_cmd.WhichOneof("payload"), name)

    def test_event_cmd_and_resp(self):
        w = custom_commands_pb2.CommandWrapper()
        w.event_cmd.cmd = custom_commands_pb2.EventGet
        self.assertEqual(w.SerializeToString(), bytes.fromhex("2a00"))  # EventGet=0 absent
        r = custom_commands_pb2.CommandWrapper()
        r.event_resp.json = '{"et":"DeviceBound","ec":0,"o":"abc"}'
        r.event_resp.number_of_events = 2
        rr = custom_commands_pb2.CommandWrapper.FromString(r.SerializeToString())
        self.assertEqual(rr.WhichOneof("payload"), "event_resp")
        self.assertEqual(rr.event_resp.number_of_events, 2)

    def test_status_enums(self):
        self.assertEqual(custom_commands_pb2.Status.Name(0), "Success")
        self.assertEqual(custom_commands_pb2.Status.Name(1), "Fail")
        self.assertEqual(constants_pb2.Status.Name(6), "CryptoError")


class TestWifiProtos(unittest.TestCase):
    def test_scan_start_matches_rust_params(self):
        p = wifi_scan_pb2.WiFiScanPayload(msg=wifi_scan_pb2.TypeCmdScanStart)
        p.cmd_scan_start.blocking = True
        p.cmd_scan_start.passive = False
        p.cmd_scan_start.group_channels = 0
        p.cmd_scan_start.period_ms = 120
        b = p.SerializeToString()
        # msg=0 absent; field 10 (cmd_scan_start) len 4: 08 01 (blocking) 20 78 (period_ms=120)
        self.assertEqual(b, bytes.fromhex("52 04 08 01 20 78".replace(" ", "")))
        r = wifi_scan_pb2.WiFiScanPayload.FromString(b)
        self.assertEqual(r.WhichOneof("payload"), "cmd_scan_start")
        self.assertEqual(r.cmd_scan_start.period_ms, 120)

    def test_scan_result_round_trip(self):
        p = wifi_scan_pb2.WiFiScanPayload(msg=wifi_scan_pb2.TypeRespScanResult, status=constants_pb2.Success)
        e = p.resp_scan_result.entries.add()
        e.ssid, e.channel, e.rssi, e.bssid, e.auth = b"Net", 6, -55, bytes.fromhex("aabbccddeeff"), wifi_constants_pb2.WPA2_WPA3_PSK
        r = wifi_scan_pb2.WiFiScanPayload.FromString(p.SerializeToString())
        self.assertEqual(r.resp_scan_result.entries[0].rssi, -55)
        self.assertEqual(wifi_constants_pb2.WifiAuthMode.Name(r.resp_scan_result.entries[0].auth), "WPA2_WPA3_PSK")

    def test_set_config_round_trip(self):
        p = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeCmdSetConfig)
        p.cmd_set_config.ssid = "My Net".encode()
        p.cmd_set_config.passphrase = "s3cret!".encode()
        b = p.SerializeToString()
        self.assertEqual(b[:2], bytes([0x08, 0x02]))  # msg = TypeCmdSetConfig (2)
        r = wifi_config_pb2.WiFiConfigPayload.FromString(b)
        self.assertEqual(r.cmd_set_config.passphrase, b"s3cret!")

    def test_get_status_oneof_presence_with_zero_enum(self):
        """fail_reason = AuthError (0) must still be detectable via the oneof."""
        p = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeRespGetStatus)
        p.resp_get_status.sta_state = wifi_constants_pb2.ConnectionFailed
        p.resp_get_status.fail_reason = wifi_constants_pb2.AuthError
        r = wifi_config_pb2.WiFiConfigPayload.FromString(p.SerializeToString())
        self.assertEqual(r.resp_get_status.WhichOneof("state"), "fail_reason")
        self.assertEqual(r.resp_get_status.fail_reason, 0)
        q = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeRespGetStatus)
        q.resp_get_status.sta_state = wifi_constants_pb2.Connected  # 0 -> absent
        q.resp_get_status.connected.ip4_addr = "10.0.0.2"
        r2 = wifi_config_pb2.WiFiConfigPayload.FromString(q.SerializeToString())
        self.assertEqual(r2.resp_get_status.WhichOneof("state"), "connected")
        self.assertEqual(r2.resp_get_status.sta_state, 0)


if __name__ == "__main__":
    unittest.main()
