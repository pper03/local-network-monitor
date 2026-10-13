import copy
import csv
import ipaddress
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import network
from storage import Store, DEFAULT_SETTINGS


def sample(mac='AA:BB:CC:DD:EE:01', ip='192.168.1.2'):
    return {'timestamp': '2026-10-10T12:00:00+07:00',
            'info': {'network': '192.168.1.0/24', 'gateway': '192.168.1.1', 'ip': ip, 'mac': mac, 'interface_index': 1},
            'devices': [{'ip': ip, 'mac': mac, 'hostname': 'test-pc', 'device_name': 'This PC'}]}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_first_scan_rename_and_second_scan(self):
        first = self.store.record_scan(sample())
        self.assertEqual(first['new_count'], 1)
        self.store.rename('AA:BB:CC:DD:EE:01', 'My PC')
        second = self.store.record_scan(sample())
        self.assertEqual(second['known_count'], 1)
        self.assertEqual(second['devices'][0]['device_name'], 'My PC')
        self.assertEqual(Store(self.temp.name).data, self.store.data)

    def test_ip_identity_upgrades_to_mac(self):
        self.store.record_scan(sample(mac='-'))
        self.store.rename('IP:192.168.1.2', 'Named PC')
        scan = self.store.record_scan(sample())
        self.assertEqual(len(self.store.data['devices']), 1)
        self.assertEqual(scan['new_count'], 0)
        self.assertEqual(scan['devices'][0]['device_name'], 'Named PC')

    def test_missing_device_not_detected_and_other_network_unknown(self):
        self.store.record_scan(sample())
        result = sample();result['devices'] = []
        self.store.record_scan(result)
        self.assertEqual(next(iter(self.store.data['devices'].values()))['status'], 'Not detected')
        result['info']['network'] = '10.0.0.0/24'
        self.store.record_scan(result)
        self.assertEqual(next(iter(self.store.data['devices'].values()))['status'], 'Unknown')

    def test_failed_save_does_not_change_memory_or_file(self):
        self.store.record_scan(sample())
        before = copy.deepcopy(self.store.data)
        original = self.store.path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):self.store.rename('AA:BB:CC:DD:EE:01', 'Lost')
        self.assertEqual(self.store.data, before)
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_history_limit_and_clear_preserve_devices(self):
        self.store.update_settings({**DEFAULT_SETTINGS, 'history_limit': 10})
        for _ in range(12):self.store.record_scan(sample())
        self.assertEqual(len(self.store.data['history']), 10)
        self.store.clear_history()
        self.assertEqual(len(self.store.data['devices']), 1)
        self.assertEqual(self.store.data['history'], [])

    def test_corrupt_file_is_preserved(self):
        self.store.path.write_text('broken JSON')
        recovered = Store(self.temp.name)
        self.assertTrue(recovered.warning)
        self.assertEqual(len(list(Path(self.temp.name).glob('data-invalid-*.json'))), 1)

    def test_legacy_import(self):
        legacy = Path(self.temp.name) / 'devices.json'
        legacy.write_text(json.dumps({'old': {'custom_name': 'Old PC', 'last_ip': '1.2.3.4'}}))
        migrated = Store(Path(self.temp.name) / 'new', legacy)
        self.assertEqual(migrated.data['devices']['old']['custom_name'], 'Old PC')
        self.assertTrue(legacy.exists())


class NetworkTests(unittest.TestCase):
    def test_ping_unreachable_is_not_online(self):
        with patch('network.run_command', return_value=subprocess.CompletedProcess([], 0, 'Reply from 192.168.1.1: Destination host unreachable.', '')):
            self.assertFalse(network.ping_host('192.168.1.2', 500))
        with patch('network.run_command', return_value=subprocess.CompletedProcess([], 0, 'Reply from 192.168.1.2: bytes=32 time<1ms TTL=128', '')):
            self.assertTrue(network.ping_host('192.168.1.2', 500))

    def test_large_network_rejected_before_enumerating(self):
        info = sample()['info'];info['network'] = '10.0.0.0/8'
        with patch('network.get_network_info', return_value=info), patch.object(ipaddress.IPv4Network, 'hosts', side_effect=AssertionError('must not enumerate')):
            with self.assertRaisesRegex(RuntimeError, 'at most'):
                network.scan_network(DEFAULT_SETTINGS, threading.Event())

    def test_cancellation_before_network_access(self):
        stop = threading.Event();stop.set()
        with patch('network.get_network_info', side_effect=AssertionError('must not run')):
            with self.assertRaises(network.ScanCancelled):network.scan_network(DEFAULT_SETTINGS, stop)

    def test_scan_does_not_invent_gateway_online(self):
        info = sample()['info'];info['network'] = '192.168.1.0/30'
        with patch('network.get_network_info', return_value=info), patch('network.ping_host', return_value=False), patch('network.get_neighbors', return_value={}):
            result = network.scan_network({**DEFAULT_SETTINGS, 'resolve_hostnames': False}, threading.Event())
        self.assertEqual([d['ip'] for d in result['devices']], ['192.168.1.2'])

    def test_mac_validation(self):
        self.assertEqual(network.normalize_mac('aa-bb-cc-dd-ee-01'), 'AA:BB:CC:DD:EE:01')
        self.assertEqual(network.normalize_mac('00-00-00-00-00-00'), '-')


if __name__ == '__main__':
    unittest.main()
