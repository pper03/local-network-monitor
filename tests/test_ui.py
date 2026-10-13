import csv
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QWidget, QInputDialog
from main import Monitor, STYLE, export_csv
from storage import Store
from network import ScanCancelled
from test_core import sample


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion');cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.monitor = Monitor(self.app, Store(self.temp.name), scanner=lambda *args: sample())
        self.monitor.window.show();self.app.processEvents()
        self.monitor.show_error = lambda title, message: self.errors.append((title, message))
        self.errors = []

    def tearDown(self):
        if self.monitor.thread:
            self.monitor.cancel_scan();self.wait_scan()
        self.monitor.window.close();self.app.processEvents()
        self.temp.cleanup()

    def wait_scan(self):
        deadline = time.monotonic() + 5
        while self.monitor.thread is not None and time.monotonic() < deadline:
            self.app.processEvents();time.sleep(.005)
        self.assertIsNone(self.monitor.thread)

    def test_layout_navigation_and_fixed_columns(self):
        m = self.monitor
        for width, height in ((1100, 750), (1600, 950)):
            m.window.resize(width, height);self.app.processEvents()
            self.assertEqual(m.stack.width(), m.window.centralWidget().width() - 250)
            self.assertEqual(m.find(QWidget, 'onlineCard').height(), 120)
            self.assertEqual(m.dashboard_table.columnWidth(0), 120)
        for name, page in m.pages.items():
            m.show_page(name);self.assertIs(m.stack.currentWidget(), page)

    def test_scan_search_rename_history_and_settings(self):
        m = self.monitor;m.start_scan();self.wait_scan()
        self.assertFalse(self.errors)
        self.assertEqual(m.dashboard_table.rowCount(), 1)
        self.assertEqual(m.history_table.rowCount(), 1)
        m.search.setText('nonexistent');self.assertEqual(m.devices_table.rowCount(), 0)
        m.search.clear();self.assertEqual(m.devices_table.rowCount(), 1)
        with patch.object(QInputDialog, 'getText', return_value=('My PC', True)):
            m.rename_item(m.devices_table.item(0, 0), 0)
        self.assertEqual(m.dashboard_table.item(0, 1).text(), 'My PC')
        m.auto_scan.setChecked(True);m.interval.setValue(30);m.save_settings()
        self.assertTrue(m.timer.isActive())
        self.assertEqual(Store(self.temp.name).settings['interval_seconds'], 30)

    def test_cancel_and_close_wait_for_worker_without_saving(self):
        m = self.monitor
        def scanner(settings, stop, progress):
            while not stop.wait(.01):pass
            raise ScanCancelled()
        m.scanner = scanner;m.start_scan();m.window.close()
        self.assertTrue(m.window.isVisible())
        self.wait_scan()
        self.assertFalse(m.window.isVisible())
        self.assertEqual(m.store.data['history'], [])

    def test_scan_error_recovers_button(self):
        def failed(*args):raise RuntimeError('No adapter')
        m = self.monitor;m.scanner = failed;m.start_scan();self.wait_scan()
        self.assertEqual(self.errors[0], ('Scan failed', 'No adapter'))
        self.assertTrue(m.scan_button.isEnabled())
        self.assertEqual(m.store.data['history'], [])

    def test_csv_unicode_and_formula_neutralization(self):
        path = Path(self.temp.name) / 'export.csv'
        export_csv(path, ['Name'], [['เครื่องของ P'], ['=1+2']])
        with path.open(encoding='utf-8-sig', newline='') as handle:rows = list(csv.reader(handle))
        self.assertEqual(rows[1][0], 'เครื่องของ P')
        self.assertEqual(rows[2][0], "'=1+2")


if __name__ == '__main__':unittest.main()
