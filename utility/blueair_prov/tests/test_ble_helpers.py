import unittest
from types import SimpleNamespace

from blueair_prov import constants as C
from blueair_prov.ble import ConnectOptions, is_blueair, is_connectable


def adv(name="112637_10BDA359C5E0", uuids=(C.SERVICE_UUID,), connectable=None, rssi=-50):
    d = {} if connectable is None else {"kCBAdvDataIsConnectable": connectable}
    return SimpleNamespace(local_name=name, service_uuids=list(uuids), rssi=rssi, tx_power=None,
                           manufacturer_data={}, platform_data=(None, d, rssi))


def dev(address="79B790D6-D9EB-5C10-7B01-B84D0C404781", name=None):
    return SimpleNamespace(address=address, name=name, details=(None, None))


class TestHelpers(unittest.TestCase):
    def test_is_connectable(self):
        self.assertIs(is_connectable(adv(connectable=0)), False)
        self.assertIs(is_connectable(adv(connectable=1)), True)
        self.assertIsNone(is_connectable(adv()))
        self.assertIsNone(is_connectable(SimpleNamespace(platform_data=None)))

    def test_match_by_service_uuid(self):
        self.assertTrue(is_blueair(dev(), adv(), ConnectOptions()))
        self.assertFalse(is_blueair(dev(), adv(uuids=()), ConnectOptions()))

    def test_match_by_name_prefix_without_service(self):
        self.assertTrue(is_blueair(dev(), adv(uuids=()), ConnectOptions(name_prefix="112637_")))
        self.assertFalse(is_blueair(dev(), adv(name="Other", uuids=()), ConnectOptions(name_prefix="112637_")))

    def test_pinned_address_and_name(self):
        self.assertTrue(is_blueair(dev(), adv(), ConnectOptions(address="79b790d6-d9eb-5c10-7b01-b84d0c404781")))
        self.assertFalse(is_blueair(dev(address="X"), adv(), ConnectOptions(address="Y")))
        self.assertTrue(is_blueair(dev(), adv(), ConnectOptions(name="112637_10BDA359C5E0")))
        self.assertFalse(is_blueair(dev(), adv(), ConnectOptions(name="nope")))


if __name__ == "__main__":
    unittest.main()
