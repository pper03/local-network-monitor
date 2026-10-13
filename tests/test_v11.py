import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint, QLocale
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLabel, QInputDialog
from main import Monitor
from storage import Store
from test_core import sample


class UpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.record_scan(sample())
        second = sample();second['timestamp'] = '2026-10-10T19:45:00+07:00'
        self.store.record_scan(second)
        self.m = Monitor(self.app, self.store)
        self.m.window.show();self.app.processEvents()

    def tearDown(self):
        self.m.theme_timer.stop();self.m.timer.stop()
        self.m.window.close();self.app.processEvents()
        self.temp.cleanup()

    def test_titles_align_and_controls_stay_compact(self):
        m=self.m;positions=[]
        for key in m.pages:
            m.show_page(key);self.app.processEvents()
            title = m.find(QLabel, 'dashboardTitleLabel') if key == 'dashboard' else m.pages[key].layout().itemAt(0).widget()
            positions.append(title.mapTo(m.stack, QPoint(0, 0)).toTuple())
        self.assertEqual(len(set(positions)), 1, positions)
        for size in ((1100,750),(1700,1000)):
            m.window.resize(*size);self.app.processEvents()
            self.assertEqual(m.search.width(),320)
            self.assertEqual(m.interval.width(),190)
        self.assertEqual(m.interval.locale().zeroDigit(),'0')
        self.assertNotIn('๖',m.interval.text())

    def test_doubleclick_copies_and_context_rename_works(self):
        m=self.m;item=m.devices_table.item(0,0)
        with patch.object(QInputDialog,'getText',side_effect=AssertionError('Double-click must not rename')):
            m.devices_table.itemDoubleClicked.emit(item)
        self.assertEqual(self.app.clipboard().text(),item.text())
        menu=m.create_table_menu(m.devices_table,0,0)
        with patch.object(QInputDialog,'getText',return_value=('Renamed',True)):
            next(a for a in menu.actions() if a.text()=='Rename device...').trigger()
        self.assertEqual(m.devices_table.item(0,0).text(),'Renamed')
        menu.deleteLater()

    def test_history_filter_and_new_scan_markers(self):
        m=self.m
        self.assertEqual(m.history_table.rowCount(),2)
        self.assertIn('★',m.history_table.item(1,4).text())
        self.assertNotIn('★',m.history_table.item(0,4).text())
        m.history_search.setText('19:45')
        self.assertEqual(m.history_table.rowCount(),1)
        self.assertEqual(m.history_details.rowCount(),1)
        m.history_search.setText('no results')
        self.assertEqual(m.history_details.rowCount(),0)
        m.history_search.clear();self.assertEqual(m.history_table.rowCount(),2)

    def test_switch_persists_immediately_and_is_in_sidebar(self):
        m=self.m;m.auto_scan.click()
        self.assertTrue(Store(self.temp.name).settings['auto_scan'])
        self.assertTrue(m.timer.isActive())
        self.assertEqual(m.auto_state.text(),'ON')
        self.assertEqual(m.auto_scan.parentWidget().parentWidget().objectName(),'sidebarFrame')
        m.auto_scan.click();self.assertFalse(m.timer.isActive())

    def test_themes_custom_colors_and_existing_data_survive(self):
        m=self.m
        for theme in ['Dark','Light','Same as device theme','Custom']:
            m.theme_combo.setCurrentText(theme)
            self.assertEqual(Store(self.temp.name).settings['theme'],theme)
        with patch('main.QColorDialog.getColor',return_value=QColor('#FFAA55')):
            m.pick_color('accent')
        self.assertEqual(Store(self.temp.name).settings['custom_colors']['accent'],'#ffaa55')
        m.save_settings()
        self.assertEqual(m.store.settings['theme'],'Custom')
        self.assertEqual(len(m.store.data['history']),2)
        self.assertEqual(len(m.store.data['devices']),1)

    def test_system_theme_change_is_applied(self):
        m=self.m
        with patch('appearance.system_is_dark',return_value=False), patch('main.system_is_dark',return_value=False):
            m.theme_combo.setCurrentText('Same as device theme')
            self.assertFalse(m.theme_colors['dark'])
        with patch('appearance.system_is_dark',return_value=True), patch('main.system_is_dark',return_value=True):
            m.check_device_theme()
            self.assertTrue(m.theme_colors['dark'])


if __name__=='__main__':unittest.main()
