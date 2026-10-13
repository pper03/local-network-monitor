"""Local Network Monitor v1.1 — Qt desktop application for Windows."""
import csv
import ipaddress
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFile, QObject, QThread, Signal, Slot, Qt, QEvent, QTimer, QUrl, QLockFile, QLocale
from PySide6.QtGui import QDesktopServices, QColor, QBrush, QKeySequence, QShortcut
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QStackedWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QCheckBox, QSpinBox, QProgressBar, QMessageBox, QFileDialog,
    QInputDialog, QAbstractItemView, QFrame, QMenu, QColorDialog, QSizePolicy, QListView)

from appearance import ToggleSwitch, apply_theme, system_is_dark, CUSTOM_DEFAULTS

from network import scan_network, ScanCancelled
from storage import Store, default_data_dir

VERSION = "1.1.0"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PROGRAM_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
STYLE = '''
QWidget { font-family: "Segoe UI", sans-serif; font-size: 13px; color: #172033; }
QMainWindow, QStackedWidget { background: #F4F6FA; }
QLineEdit, QComboBox, QSpinBox { background: white; border: 1px solid #CFD7E3; border-radius: 6px; padding: 7px; }
QPushButton { background: #E6EDF7; border: none; border-radius: 6px; padding: 9px 14px; }
QPushButton:hover { background: #D5E3F8; }
QPushButton:disabled { color: #8993A4; background: #E5E7EB; }
QPushButton:checked { background: #2563EB; color: white; }
QTableWidget { background: white; alternate-background-color: #F8FAFC; gridline-color: #E2E8F0; border: 1px solid #DDE3EC; }
QHeaderView::section { background: #EDF2F8; padding: 9px; border: 0; border-right: 1px solid #DDE3EC; border-bottom: 1px solid #DDE3EC; font-weight: bold; }
QTableWidget::item:selected { background: #DBEAFE; color: #172033; }
QProgressBar { border: 1px solid #DDE3EC; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #60A5FA; }
QCheckBox { spacing: 9px; }
'''


def display_time(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value or "-")


def export_csv(path, headers, rows):
    # Neutralize spreadsheet formulas in names and other externally supplied text.
    def cell(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) and text != "-" else text
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows([[cell(value) for value in row] for row in rows])


class ScanWorker(QObject):
    result = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    progress = Signal(int, int, str)
    done = Signal()

    def __init__(self, settings, cancel, scanner):
        super().__init__()
        self.settings, self.cancel, self.scanner = dict(settings), cancel, scanner

    @Slot()
    def run(self):
        try:
            result = self.scanner(self.settings, self.cancel, self.progress.emit)
            if self.cancel.is_set():
                self.cancelled.emit()
            else:
                self.result.emit(result)
        except ScanCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.error.emit(str(error))
        finally:
            self.done.emit()


class Monitor(QObject):
    def __init__(self, app, store=None, scanner=scan_network):
        super().__init__()
        self.app = app
        self.app.setStyle("Fusion")
        QLocale.setDefault(QLocale.c())
        self.store = store if store is not None else Store(legacy_path=PROGRAM_DIR / "devices.json")
        self.scanner = scanner
        self.thread = None
        self.worker = None
        self.cancel = None
        self.closing = False
        self.current = None
        self.session_scanned = False
        self.active_page = "dashboard"
        self.history_snapshots = []
        source = QFile(str(RESOURCE_DIR / "main_window.ui"))
        if not source.open(QFile.ReadOnly):
            raise RuntimeError("main_window.ui is missing. Keep it beside main.py.")
        loader = QUiLoader()
        self.window = loader.load(source)
        source.close()
        if self.window is None:
            raise RuntimeError(loader.errorString())
        self.setParent(self.window)
        self.window.installEventFilter(self)
        self.window.setWindowTitle(f"Local Network Monitor  v{VERSION}")
        self.window.setMinimumSize(1060, 720)
        self.stack = self.find(QStackedWidget, "mainStack")
        self.dashboard = self.find(QWidget, "dashboardPage")
        self.devices_page = self.find(QWidget, "devicesPage")
        self.dashboard_table = self.find(QTableWidget, "devicesTable")
        self.devices_table = self.find(QTableWidget, "allDevicesTable")
        self.scan_button = self.find(QPushButton, "scanButton")
        self.scan_button.clicked.connect(self.scan_or_cancel)
        self.status = self.window.statusBar()
        self.progress = QProgressBar()
        self.progress.setFixedWidth(200)
        self.progress.hide()
        self.status.addPermanentWidget(self.progress)
        self.configure_table(self.dashboard_table, {0: 120, 1: 130, 4: 100, 5: 175})
        self.configure_table(self.devices_table, {0: 150, 1: 120, 2: 165, 4: 115})
        self.find(QLabel, "devicesTitleLabel").setText("Detected Devices — Last Scan")
        self.find(QLabel, "onlineTextLabel").setText("Detected / Last Scan")
        self.find(QLabel, "networkLabel").setWordWrap(True)
        self.find(QLabel, "gatewayLabel").setWordWrap(True)
        self.find(QLabel, "lastScanLabel").setWordWrap(True)
        self.create_devices_controls()
        self.create_history()
        self.create_settings()
        self.create_auto_switch()
        self.pages = {"dashboard": self.dashboard, "devices": self.devices_page,
                      "history": self.history_page, "settings": self.settings_page}
        self.buttons = {}
        for name in self.pages:
            button = self.find(QPushButton, name + "Button")
            button.setEnabled(True)
            button.setCheckable(True)
            button.setStyleSheet('QPushButton { text-align: left; color: #CBD5E1; background: #1B2639; padding: 10px 14px; border-radius: 7px; } QPushButton:hover { background: #283952; } QPushButton:checked { background: #2563EB; color: white; }')
            button.clicked.connect(lambda checked=False, key=name: self.show_page(key))
            self.buttons[name] = button
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.start_scan)
        self.fill_settings()
        self.normalize_ui()
        self.apply_appearance()
        self.theme_timer = QTimer(self)
        self.theme_timer.timeout.connect(self.check_device_theme)
        self.theme_timer.start(2000)
        self.refresh_devices()
        self.refresh_history()
        if self.store.data["history"]:
            self.display_snapshot(self.store.data["history"][-1], saved=True)
        self.show_page("dashboard")
        self.status.showMessage("Ready — scan your local network to refresh device status.")
        self.schedule_next()
        if self.store.warning:
            QTimer.singleShot(0, lambda: QMessageBox.warning(self.window, "Saved data", self.store.warning))

    def find(self, cls, name):
        widget = self.window.findChild(cls, name)
        if widget is None:
            raise RuntimeError(f"UI element missing: {name}")
        return widget

    def configure_table(self, table, fixed=None):
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda point, t=table: self.table_menu(t, point))
        table.itemDoubleClicked.connect(self.copy_cell)
        shortcut = QShortcut(QKeySequence.Copy, table)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda t=table: self.copy_cell(t.currentItem()))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(38)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(90)
        for index in range(table.columnCount()):
            header.setSectionResizeMode(index, QHeaderView.Stretch)
        for index, width in (fixed or {}).items():
            header.setSectionResizeMode(index, QHeaderView.Fixed)
            table.setColumnWidth(index, width)

    def make_table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        self.configure_table(table)
        return table

    def fill_table(self, table, rows, ids=None):
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                item.setTextAlignment(Qt.AlignCenter)
                if ids:
                    item.setData(Qt.UserRole, ids[row])
                table.setItem(row, column, item)

    def new_page(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(18)
        label = QLabel(title)
        label.setProperty("role", "title")
        label.setFixedHeight(42)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(label)
        self.stack.addWidget(page)
        return page, layout

    def create_devices_controls(self):
        layout = self.devices_page.layout()
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, IP, hostname or MAC...")
        self.search.setFixedWidth(320)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_devices)
        self.status_filter = QComboBox()
        self.status_filter.setFixedWidth(155)
        self.status_filter.setView(QListView())
        self.status_filter.addItems(["All statuses", "Online", "Not detected", "Unknown"])
        self.status_filter.currentTextChanged.connect(self.refresh_devices)
        rename = QPushButton("Rename")
        rename.clicked.connect(self.rename_selected)
        export = QPushButton("Export CSV")
        export.clicked.connect(lambda: self.export_table(self.devices_table, "devices.csv"))
        for widget in (self.search, self.status_filter, rename, export):
            controls.addWidget(widget)
        controls.addStretch()
        layout.insertLayout(1, controls)
        note = QLabel("Double-click a cell to copy. Right-click for actions. Not detected can mean offline or blocked ping.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748B;")
        layout.insertWidget(2, note)
        for index in range(layout.count()):
            layout.setStretch(index, 1 if layout.itemAt(index).widget() is self.devices_table else 0)

    def create_history(self):
        self.history_page, layout = self.new_page("Scan History")
        self.history_summary = QLabel()
        layout.addWidget(self.history_summary)
        controls = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Find date / time: 2026-10-10 or 19:45")
        self.history_search.setFixedWidth(320)
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self.refresh_history)
        controls.addWidget(self.history_search)
        export = QPushButton("Export Selected Scan")
        export.clicked.connect(lambda: self.export_table(self.history_details, "scan-history.csv"))
        clear = QPushButton("Clear History")
        clear.clicked.connect(self.clear_history)
        controls.addWidget(export)
        controls.addStretch()
        controls.addWidget(clear)
        layout.addLayout(controls)
        self.history_table = self.make_table(["Scan Time", "Network", "Detected", "Known", "New"])
        self.history_table.itemSelectionChanged.connect(self.select_history)
        layout.addWidget(self.history_table, 1)
        layout.addWidget(QLabel("Devices detected in the selected scan"))
        self.history_details = self.make_table(["IP Address", "Device Name", "Hostname", "MAC Address"])
        layout.addWidget(self.history_details, 2)

    def create_settings(self):
        self.settings_page, layout = self.new_page("Settings")
        form = QFormLayout()
        form.setSpacing(18)
        form.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        self.interval = QSpinBox(); self.interval.setRange(15, 3600); self.interval.setSuffix(" seconds")
        self.timeout = QSpinBox(); self.timeout.setRange(100, 2000); self.timeout.setSingleStep(100); self.timeout.setSuffix(" ms")
        self.resolve_names = QCheckBox("Look up hostnames (may slow down scans)")
        self.history_limit = QSpinBox(); self.history_limit.setRange(10, 500); self.history_limit.setSuffix(" scans")
        form.addRow("Wait after each scan", self.interval)
        form.addRow("Ping timeout", self.timeout)
        form.addRow("Name lookup", self.resolve_names)
        form.addRow("Keep history", self.history_limit)
        for spin in (self.interval, self.timeout, self.history_limit):
            spin.setLocale(QLocale.c())
            spin.setFixedWidth(190)
            spin.setFixedHeight(38)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Light", "Dark", "Same as device theme", "Custom"])
        self.theme_combo.setFixedWidth(240)
        self.theme_combo.setView(QListView())
        form.addRow("Theme", self.theme_combo)
        self.custom_colors = dict(self.store.settings["custom_colors"])
        self.color_controls = QWidget()
        color_row = QHBoxLayout(self.color_controls)
        color_row.setContentsMargins(0, 0, 0, 0)
        self.color_buttons = {}
        for key, title in (("background", "Background"), ("surface", "Cards / tables"), ("accent", "Accent")):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, k=key: self.pick_color(k))
            color_row.addWidget(button)
            self.color_buttons[key] = button
        form.addRow("Custom colors", self.color_controls)
        self.theme_combo.currentTextChanged.connect(self.theme_changed)
        layout.addLayout(form)
        controls = QHBoxLayout()
        save = QPushButton("Save Settings"); save.clicked.connect(self.save_settings)
        open_folder = QPushButton("Open Data Folder"); open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory))))
        controls.addWidget(save); controls.addWidget(open_folder); controls.addStretch()
        layout.addLayout(controls)
        details = QLabel("Local Network Monitor v1.1.0\n\n"
            "Auto Scan is above the Scan Network button in the sidebar. The switch saves immediately.\n"
            "Scan timing, name lookup and history limit use Save Settings. Theme changes save immediately.\n\n"
            "IPv4 discovery: up to 512 hosts per scan. Devices blocking ping may not be detected.\n"
            "Saved data stays on this computer. Cancelled scans are not saved.\n\n"
            f"Data folder: {self.store.directory}")
        details.setWordWrap(True)
        details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.setStyleSheet("color: #64748B; line-height: 1.5;")
        layout.addWidget(details)
        layout.addStretch()

    def fill_settings(self):
        settings = self.store.settings
        self.auto_scan.blockSignals(True)
        self.auto_scan.setChecked(settings["auto_scan"])
        self.auto_scan.blockSignals(False)
        self.auto_state.setText("ON" if self.auto_scan.isChecked() else "OFF")
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentText(settings["theme"])
        self.theme_combo.blockSignals(False)
        self.interval.setValue(settings["interval_seconds"])
        self.timeout.setValue(settings["ping_timeout_ms"])
        self.resolve_names.setChecked(settings["resolve_hostnames"])
        self.history_limit.setValue(settings["history_limit"])

    @Slot()
    def save_settings(self):
        try:
            self.store.update_settings({**self.store.settings, "auto_scan": self.auto_scan.isChecked(), "interval_seconds": self.interval.value(),
                "ping_timeout_ms": self.timeout.value(), "resolve_hostnames": self.resolve_names.isChecked(),
                "history_limit": self.history_limit.value()})
        except OSError as error:
            self.show_error("Could not save settings", str(error)); return
        self.refresh_history()
        self.schedule_next()
        self.status.showMessage("Settings saved")

    def schedule_next(self):
        self.timer.stop()
        if self.store.settings["auto_scan"] and self.thread is None and not self.closing:
            self.timer.start(self.store.settings["interval_seconds"] * 1000)

    def show_page(self, name):
        self.active_page = name
        self.stack.setCurrentWidget(self.pages[name])
        for key, button in self.buttons.items():
            button.setChecked(key == name)

    @Slot()
    def scan_or_cancel(self):
        if self.thread is not None:
            self.cancel_scan()
        else:
            self.start_scan()

    @Slot()
    def start_scan(self):
        if self.thread is not None or self.closing:
            return
        self.timer.stop()
        self.cancel = threading.Event()
        self.thread = QThread(self)
        self.worker = ScanWorker(self.store.settings, self.cancel, self.scanner)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self.scan_complete)
        self.worker.error.connect(self.scan_failed)
        self.worker.cancelled.connect(self.scan_cancelled)
        self.worker.progress.connect(self.scan_progress)
        self.worker.done.connect(self.thread.quit)
        self.worker.done.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.scan_cleanup)
        self.thread.finished.connect(self.thread.deleteLater)
        self.scan_button.setText("Cancel Scan")
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.showMessage("Detecting network...")
        self.thread.start()

    def cancel_scan(self):
        if self.cancel is not None:
            self.cancel.set()
            self.scan_button.setEnabled(False)
            self.scan_button.setText("Cancelling...")
            self.status.showMessage("Cancelling — waiting for active network commands to finish...")

    @Slot(int, int, str)
    def scan_progress(self, done, total, phase):
        if self.cancel is not None and self.cancel.is_set():
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.status.showMessage(f"{phase}: {done}/{total}" if total else phase)

    @Slot(object)
    def scan_complete(self, result):
        if self.cancel.is_set():
            self.scan_cancelled(); return
        try:
            snapshot = self.store.record_scan(result)
        except (OSError, ValueError) as error:
            self.show_error("Could not save scan", str(error)); return
        self.session_scanned = True
        self.display_snapshot(snapshot)
        self.refresh_devices()
        self.refresh_history()
        self.status.showMessage(f"Scan complete — {len(snapshot['devices'])} devices detected, {snapshot['new_count']} new")

    def display_snapshot(self, snapshot, saved=False):
        self.current = snapshot
        info = snapshot["info"]
        self.find(QLabel, "networkLabel").setText("Network: " + info["network"])
        self.find(QLabel, "gatewayLabel").setText("Gateway: " + info["gateway"])
        self.find(QLabel, "lastScanLabel").setText(("Saved Scan: " if saved else "Last Scan: ") + display_time(snapshot["timestamp"]))
        for name, value in (("onlineCountLabel", len(snapshot["devices"])), ("knownCountLabel", snapshot["known_count"]), ("newCountLabel", snapshot["new_count"])):
            self.find(QLabel, name).setText(str(value))
        self.refresh_dashboard()

    def refresh_dashboard(self):
        if not self.current:
            return
        rows, ids = [], []
        for device in self.current["devices"]:
            record = self.store.data["devices"].get(device["id"], {})
            name = record.get("custom_name") or device["device_name"]
            rows.append([device["ip"], name, device["hostname"], device["mac"], "Online" if self.session_scanned else "Saved", display_time(device["last_seen"])])
            ids.append(device["id"])
        self.fill_table(self.dashboard_table, rows, ids)

    @Slot()
    def refresh_devices(self):
        query = self.search.text().strip().casefold()
        status_filter = self.status_filter.currentText()
        rows, ids = [], []
        records = sorted(self.store.data["devices"].items(), key=lambda pair: (pair[1].get("custom_name") or pair[1].get("device_name", "")).casefold())
        for key, device in records:
            status = device.get("status", "Unknown") if self.session_scanned else "Unknown"
            values = [device.get("custom_name") or device.get("device_name", "Unknown"), device.get("last_ip", "-"),
                      device.get("mac", "-"), device.get("hostname", "-"), status,
                      display_time(device.get("first_seen")), display_time(device.get("last_seen"))]
            if status_filter != "All statuses" and status != status_filter:
                continue
            if query and query not in " ".join(values).casefold():
                continue
            rows.append(values); ids.append(key)
        self.fill_table(self.devices_table, rows, ids)

    def rename_selected(self):
        row = self.devices_table.currentRow()
        if row < 0:
            self.status.showMessage("Select a device to rename first."); return
        self.rename_item(self.devices_table.item(row, 0), 0)

    def rename_item(self, item, name_column):
        if item is None or item.column() != name_column:
            return
        key = item.data(Qt.UserRole)
        if key not in self.store.data["devices"]:
            return
        name, ok = QInputDialog.getText(self.window, "Rename Device", "Name (max 80 characters; blank restores default):", QLineEdit.Normal, item.text())
        if not ok:
            return
        try:
            self.store.rename(key, name)
        except OSError as error:
            self.show_error("Could not rename device", str(error)); return
        self.refresh_devices()
        self.refresh_dashboard()
        self.status.showMessage("Device name saved")

    def refresh_history(self):
        row = self.history_table.currentRow()
        selected = self.history_snapshots[row]["timestamp"] if 0 <= row < len(self.history_snapshots) else None
        query = self.history_search.text().strip().casefold()
        self.history_snapshots = [scan for scan in reversed(self.store.data["history"])
                                  if query in display_time(scan["timestamp"]).casefold()]
        rows = [[display_time(scan["timestamp"]), scan["info"]["network"], len(scan["devices"]), scan["known_count"], scan["new_count"]] for scan in self.history_snapshots]
        self.history_table.blockSignals(True)
        self.fill_table(self.history_table, rows)
        p = getattr(self, "theme_colors", {"new_bg": "#FFF2C7", "new_text": "#694500"})
        for row, scan in enumerate(self.history_snapshots):
            if scan["new_count"]:
                for column in range(self.history_table.columnCount()):
                    item = self.history_table.item(row, column)
                    item.setBackground(QColor(p["new_bg"]))
                    item.setForeground(QColor(p["new_text"]))
                    font = item.font(); font.setBold(True); item.setFont(font)
                    item.setToolTip(item.text() + f" — {scan['new_count']} new device(s)")
                self.history_table.item(row, 4).setText(f"★ {scan['new_count']}")
        self.history_summary.setText(f"{len(rows)} / {len(self.store.data['history'])} saved scans • Gold rows / ★ mark scans with new devices")
        if rows:
            index = next((i for i, scan in enumerate(self.history_snapshots) if scan["timestamp"] == selected), 0)
            self.history_table.selectRow(index)
        self.history_table.blockSignals(False)
        self.select_history()

    def select_history(self):
        row = self.history_table.currentRow()
        rows = []
        if 0 <= row < len(self.history_snapshots):
            rows = [[d["ip"], d["device_name"], d["hostname"], d["mac"]] for d in self.history_snapshots[row]["devices"]]
        self.fill_table(self.history_details, rows)
        if 0 <= row < len(self.history_snapshots):
            p = getattr(self, "theme_colors", {"new_bg": "#FFF2C7", "new_text": "#694500"})
            for index, device in enumerate(self.history_snapshots[row]["devices"]):
                if device.get("is_new"):
                    for column in range(self.history_details.columnCount()):
                        item = self.history_details.item(index, column)
                        item.setBackground(QColor(p["new_bg"]))
                        item.setForeground(QColor(p["new_text"]))
                        item.setToolTip(item.text() + " — New in this scan")

    def clear_history(self):
        if QMessageBox.question(self.window, "Clear History", "Delete all saved scans? Remembered devices and names will be kept.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.store.clear_history()
        except OSError as error:
            self.show_error("Could not clear history", str(error)); return
        self.refresh_history()
        self.status.showMessage("History cleared; remembered devices kept")

    def export_table(self, table, filename):
        if not table.rowCount():
            self.status.showMessage("No rows to export."); return
        path, _ = QFileDialog.getSaveFileName(self.window, "Export CSV", str(Path.home() / filename), "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
        rows = [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]
        try:
            export_csv(path, headers, rows)
        except OSError as error:
            self.show_error("Export failed", str(error)); return
        self.status.showMessage(f"Exported {len(rows)} rows to {path}")

    @Slot(str)
    def scan_failed(self, message):
        if self.cancel and self.cancel.is_set():
            self.scan_cancelled(); return
        self.show_error("Scan failed", message)

    @Slot()
    def scan_cancelled(self):
        self.status.showMessage("Scan cancelled — previous data kept")

    @Slot()
    def scan_cleanup(self):
        self.thread = None
        self.worker = None
        self.cancel = None
        self.scan_button.setText("Scan Network")
        self.scan_button.setEnabled(True)
        self.progress.hide()
        if self.closing:
            self.window.close()
        else:
            self.schedule_next()

    def normalize_ui(self):
        for widget in [self.window, *self.window.findChildren(QWidget)]:
            widget.setStyleSheet("")
        self.find(QLabel, "titleLabel").setText("Local Network\nMonitor  v1.1")
        self.find(QLabel, "devicesTitleLabel").setProperty("role", "section")
        titles = [self.find(QLabel, "dashboardTitleLabel"), self.find(QLabel, "devicesPageTitleLabel")]
        titles += [self.history_page.layout().itemAt(0).widget(), self.settings_page.layout().itemAt(0).widget()]
        for title in titles:
            title.setProperty("role", "title")
            title.setFixedHeight(42)
            title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.find(QFrame, "contentFrame").setFrameShape(QFrame.NoFrame)
        dashboard_layout = self.find(QWidget, "contentFrame").layout()
        for layout in (dashboard_layout, self.devices_page.layout(), self.history_page.layout(), self.settings_page.layout()):
            layout.setContentsMargins(30, 25, 30, 25)
            layout.setSpacing(18)
        self.dashboard.layout().setContentsMargins(0, 0, 0, 0)
        self.dashboard.layout().setSpacing(0)
        for button in self.window.findChildren(QPushButton):
            if button.text() in ("Save Settings", "Export CSV", "Export Selected Scan"):
                button.setProperty("role", "primary")

    def create_auto_switch(self):
        sidebar = self.find(QWidget, "sidebarFrame")
        panel = QWidget(sidebar)
        row = QHBoxLayout(panel)
        row.setContentsMargins(6, 12, 6, 12)
        row.addWidget(QLabel("Auto Scan"))
        row.addStretch()
        self.auto_state = QLabel("OFF")
        self.auto_state.setFixedWidth(28)
        row.addWidget(self.auto_state)
        self.auto_scan = ToggleSwitch(panel)
        self.auto_scan.toggled.connect(self.toggle_auto_scan)
        row.addWidget(self.auto_scan)
        layout = sidebar.layout()
        layout.insertWidget(layout.indexOf(self.scan_button), panel)

    @Slot(bool)
    def toggle_auto_scan(self, checked):
        previous = self.store.settings["auto_scan"]
        try:
            self.store.update_settings({**self.store.settings, "auto_scan": checked})
        except OSError as error:
            self.auto_scan.blockSignals(True)
            self.auto_scan.setChecked(previous)
            self.auto_scan.blockSignals(False)
            self.show_error("Could not save Auto Scan", str(error))
            return
        self.auto_state.setText("ON" if checked else "OFF")
        self.schedule_next()
        self.status.showMessage("Auto Scan ON — next scan after the configured interval" if checked else "Auto Scan OFF — current scan is not interrupted")

    def apply_appearance(self):
        mode = self.store.settings["theme"]
        self.theme_colors = apply_theme(self.app, mode, self.store.settings["custom_colors"])
        self.last_device_dark = system_is_dark(self.app)
        self.auto_scan.accent = self.theme_colors["accent"]
        self.auto_scan.update()
        self.color_controls.setEnabled(mode == "Custom")
        for key, button in self.color_buttons.items():
            button.setToolTip(self.store.settings["custom_colors"][key])
        if hasattr(self, "history_table"):
            self.refresh_history()

    @Slot(str)
    def theme_changed(self, mode):
        previous = self.store.settings["theme"]
        try:
            self.store.update_settings({**self.store.settings, "theme": mode})
        except OSError as error:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentText(previous)
            self.theme_combo.blockSignals(False)
            self.show_error("Could not save theme", str(error))
            return
        self.apply_appearance()
        self.status.showMessage("Theme saved: " + mode)

    def pick_color(self, key):
        chosen = QColorDialog.getColor(QColor(self.store.settings["custom_colors"][key]), self.window, "Choose " + key)
        if not chosen.isValid():
            return
        custom = {**self.store.settings["custom_colors"], key: chosen.name()}
        try:
            self.store.update_settings({**self.store.settings, "custom_colors": custom, "theme": "Custom"})
        except OSError as error:
            self.show_error("Could not save color", str(error))
            return
        self.custom_colors = custom
        self.apply_appearance()
        self.status.showMessage("Custom color saved")

    def check_device_theme(self):
        if self.store.settings["theme"] == "Same as device theme" and system_is_dark(self.app) != self.last_device_dark:
            self.apply_appearance()

    def copy_cell(self, item):
        if item is not None:
            self.app.clipboard().setText(item.text())
            self.status.showMessage("Copied: " + item.text().replace("\n", " ")[:90], 2500)

    def copy_row(self, table, row):
        if row >= 0:
            values = [table.item(row, col).text() if table.item(row, col) else "" for col in range(table.columnCount())]
            self.app.clipboard().setText("\t".join(values))
            self.status.showMessage("Copied row (tab-separated)", 2500)

    def create_table_menu(self, table, row, column):
        menu = QMenu(self.window)
        item = table.item(row, column) if row >= 0 and column >= 0 else None
        action = menu.addAction("Copy cell", lambda: self.copy_cell(item))
        action.setEnabled(item is not None)
        action = menu.addAction("Copy row", lambda: self.copy_row(table, row))
        action.setEnabled(row >= 0)
        if table in (self.dashboard_table, self.devices_table):
            name_column, ip_column, mac_column = (1, 0, 3) if table is self.dashboard_table else (0, 1, 2)
            menu.addSeparator()
            for label, col in (("Copy IP address", ip_column), ("Copy MAC address", mac_column)):
                action = menu.addAction(label, lambda c=col: self.copy_cell(table.item(row, c)))
                action.setEnabled(row >= 0)
            action = menu.addAction("Rename device...", lambda: self.rename_item(table.item(row, name_column), name_column))
            action.setEnabled(row >= 0)
        if table is self.history_table:
            menu.addSeparator()
            action = menu.addAction("Show scan devices", lambda: table.selectRow(row))
            action.setEnabled(row >= 0)
            action = menu.addAction("Export this scan...", lambda: self.export_table(self.history_details, "scan-history.csv"))
            action.setEnabled(row >= 0)
        menu.addSeparator()
        action = menu.addAction("Export visible rows...", lambda: self.export_table(table, "network-export.csv"))
        action.setEnabled(table.rowCount() > 0)
        return menu

    def table_menu(self, table, point):
        index = table.indexAt(point)
        if index.isValid():
            table.setCurrentCell(index.row(), index.column())
        menu = self.create_table_menu(table, index.row(), index.column())
        menu.exec(table.viewport().mapToGlobal(point))
        menu.deleteLater()

    def show_error(self, title, message):
        self.status.showMessage(f"{title}: {message}")
        if not self.closing:
            QMessageBox.warning(self.window, title, message)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Close:
            self.timer.stop()
            if self.thread is not None:
                self.closing = True
                self.cancel_scan()
                event.ignore()
                return True
        return False


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setApplicationName("Local Network Monitor")
    app.setApplicationVersion(VERSION)
    try:
        data_dir = default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data_dir / "app.lock"))
        if not lock.tryLock(100):
            QMessageBox.information(None, "Already running", "Local Network Monitor is already open, or its data folder is not writable.")
            return 1
        monitor = Monitor(app)
    except Exception as error:
        QMessageBox.critical(None, "Unable to start", str(error))
        return 1
    monitor.window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
