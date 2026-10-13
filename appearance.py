"""Application themes and an accessible, keyboard-operable Auto Scan switch."""
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QAbstractButton

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
CUSTOM_DEFAULTS = {'background': '#16202E', 'surface': '#223044', 'accent': '#60A5FA'}


def readable(color):
    c = QColor(color)
    def linear(v):return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
    luminance = .2126 * linear(c.redF()) + .7152 * linear(c.greenF()) + .0722 * linear(c.blueF())
    return '#111827' if luminance > .179 else '#F8FAFC'


def system_is_dark(app):
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
        except OSError:
            pass
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def colors(mode, custom, app):
    dark = mode == 'Dark' or (mode == 'Same as device theme' and system_is_dark(app))
    if mode == 'Custom':
        bg, surface, accent = [custom.get(k, v) for k, v in CUSTOM_DEFAULTS.items()]
        dark = readable(surface) == '#F8FAFC'
    elif dark:
        bg, surface, accent = '#101826', '#1B2738', '#60A5FA'
    else:
        bg, surface, accent = '#EDF2F7', '#FFFFFF', '#2563EB'
    return {'bg': bg, 'surface': surface, 'accent': accent, 'text': readable(surface),
            'page_text': readable(bg), 'accent_text': readable(accent), 'dark': dark,
            'muted': '#B4C2D5' if dark else '#526176', 'border': '#46566C' if dark else '#B6C4D5',
            'alternate': QColor(surface).lighter(112).name() if dark else QColor(surface).darker(103).name(),
            'new_bg': '#493A13' if dark else '#FFF2C7', 'new_text': '#FFE6A1' if dark else '#694500'}


def apply_theme(app, mode, custom):
    p = colors(mode, custom, app)
    palette = QPalette()
    for role, value in ((QPalette.Window, p['bg']), (QPalette.WindowText, p['page_text']),
        (QPalette.Base, p['surface']), (QPalette.AlternateBase, p['alternate']),
        (QPalette.Text, p['text']), (QPalette.Button, p['surface']), (QPalette.ButtonText, p['text']),
        (QPalette.Highlight, p['accent']), (QPalette.HighlightedText, p['accent_text']),
        (QPalette.ToolTipBase, p['surface']), (QPalette.ToolTipText, p['text']),
        (QPalette.PlaceholderText, p['muted'])):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    variant = 'light' if p['dark'] else 'dark'
    up = (ROOT / 'assets' / f'up-{variant}.svg').as_posix()
    down = (ROOT / 'assets' / f'down-{variant}.svg').as_posix()
    check = (ROOT / 'assets' / f'check-{variant}.svg').as_posix()
    app.setStyleSheet(f'''
    QWidget {{ font-family: "Segoe UI", sans-serif; font-size: 13px; color: {p['text']}; }}
    QMainWindow, QWidget#centralwidget, QStackedWidget, QStackedWidget > QWidget, QFrame#contentFrame {{ background: {p['bg']}; }}
    QLabel {{ background: transparent; color: {p['page_text']}; }}
    QLabel[role="title"] {{ font-size: 28px; font-weight: 700; }}
    QLabel[role="section"] {{ font-size: 18px; font-weight: 600; }}
    QLabel[role="muted"] {{ color: {p['page_text']}; }}
    QFrame#networkInfoFrame, QFrame#onlineCard, QFrame#knownCard, QFrame#newCard {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px; }}
    QFrame#networkInfoFrame QLabel, QFrame#onlineCard QLabel, QFrame#knownCard QLabel, QFrame#newCard QLabel {{ color: {p['text']}; border: none; }}
    QLabel#onlineCountLabel, QLabel#knownCountLabel, QLabel#newCountLabel {{ font-size: 32px; font-weight: 700; }}
    QFrame#sidebarFrame {{ background: #101827; border: none; }}
    QFrame#sidebarFrame QLabel {{ color: #EDF4FF; }}
    QLabel#titleLabel {{ font-size: 19px; font-weight: bold; }}
    QPushButton {{ color: {p['text']}; background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 7px; padding: 8px 14px; font-weight: 600; min-height: 20px; }}
    QPushButton:hover {{ border: 1px solid {p['accent']}; background: {p['alternate']}; }}
    QPushButton:pressed {{ background: {p['accent']}; color: {p['accent_text']}; }}
    QPushButton:disabled {{ color: {p['muted']}; background: {p['alternate']}; }}
    QPushButton[role="primary"] {{ background: {p['accent']}; color: {p['accent_text']}; border-color: {p['accent']}; }}
    QFrame#sidebarFrame QPushButton {{ color: #DFE8F6; background: #1B293D; border: 1px solid #34465F; text-align: left; }}
    QFrame#sidebarFrame QPushButton:checked, QFrame#sidebarFrame QPushButton#scanButton {{ background: {p['accent']}; color: {p['accent_text']}; border-color: {p['accent']}; }}
    QFrame#sidebarFrame QPushButton#scanButton {{ text-align: center; }}
    QLineEdit, QComboBox, QSpinBox {{ color: {p['text']}; background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 7px 10px; min-height: 20px; selection-background-color: {p['accent']}; selection-color: {p['accent_text']}; }}
    QComboBox {{ padding-right: 32px; }}
    QComboBox::drop-down {{ width: 28px; border-left: 1px solid {p['border']}; }}
    QComboBox::down-arrow {{ image: url("{down}"); width: 12px; height: 8px; }}
    QComboBox QAbstractItemView {{ background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border']}; selection-background-color: {p['accent']}; selection-color: {p['accent_text']}; outline: none; }}
    QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 4px 8px; }}
    QSpinBox {{ padding-right: 32px; }}
    QSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 28px; height: 18px; background: {p['alternate']}; border-left: 1px solid {p['border']}; border-bottom: 1px solid {p['border']}; }}
    QSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 28px; height: 18px; background: {p['alternate']}; border-left: 1px solid {p['border']}; }}
    QSpinBox::up-arrow {{ image: url("{up}"); width: 12px; height: 8px; }}
    QSpinBox::down-arrow {{ image: url("{down}"); width: 12px; height: 8px; }}
    QCheckBox {{ color: {p['page_text']}; spacing: 10px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {p['border']}; background: {p['surface']}; }}
    QCheckBox::indicator:checked {{ image: url("{check}"); }}
    QTableWidget {{ color: {p['text']}; background: {p['surface']}; alternate-background-color: {p['alternate']}; gridline-color: {p['border']}; border: 1px solid {p['border']}; selection-background-color: {p['accent']}; selection-color: {p['accent_text']}; }}
    QTableWidget::item:selected {{ color: {p['accent_text']}; background: {p['accent']}; }}
    QHeaderView::section {{ background: {p['alternate']}; color: {p['text']}; padding: 10px 6px; border: 0; border-right: 1px solid {p['border']}; border-bottom: 1px solid {p['border']}; font-weight: 600; }}
    QMenu, QDialog, QMessageBox {{ background: {p['surface']}; color: {p['text']}; }}
    QDialog QLabel, QMessageBox QLabel {{ color: {p['text']}; }}
    QMenu {{ border: 1px solid {p['border']}; padding: 5px; }}
    QMenu::item {{ padding: 8px 26px; }}
    QMenu::item:selected {{ background: {p['accent']}; color: {p['accent_text']}; }}
    QMenu::item:disabled {{ color: {p['muted']}; }}
    QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
    QStatusBar {{ background: {p['bg']}; color: {p['page_text']}; }}
    QProgressBar {{ background: {p['surface']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 4px; text-align: center; }}
    QProgressBar::chunk {{ background: {p['accent']}; }}
    QToolTip {{ color: {p['text']}; background: {p['surface']}; border: 1px solid {p['border']}; padding: 5px; }}
    ''')
    return p


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(54, 30)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName('Auto Scan')
        self.setToolTip('Turn automatic scanning on or off')
        self.accent = '#3B82F6'
        self.toggled.connect(lambda _: self.update())

    def sizeHint(self):
        return QSize(54, 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self.accent if self.isChecked() else '#64748B'))
        painter.drawRoundedRect(QRectF(2, 3, 50, 24), 12, 12)
        painter.setBrush(QColor('#FFFFFF'))
        painter.drawEllipse(QRectF(29 if self.isChecked() else 5, 6, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor('#FFFFFF'))
            painter.drawRoundedRect(QRectF(0, 0, 53, 29), 14, 14)
