# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Colors and the application style sheet."""

BG = "#14161b"
PANEL = "#1b1e25"
PANEL_2 = "#22262f"
BORDER = "#2c313c"
TEXT = "#e3e6eb"
TEXT_DIM = "#8b93a1"
ACCENT = "#9d7bff"
ARENA = "#1f232b"
GRID = "#2a2f39"

STYLE_SHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; font-size: 9pt; }}
QToolBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 4px; }}
QPushButton, QToolButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 10px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; }}
QPushButton:checked, QToolButton:checked {{ background: #3a3150; border-color: {ACCENT}; }}
QPushButton#logAction:disabled {{ background: {PANEL_2}; color: {TEXT_DIM}; border: 1px solid {BORDER}; }}
QPushButton#logAction[busy="true"]:disabled {{ border-color: {ACCENT}; }}
QComboBox {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 8px; min-width: 320px; }}
QComboBox QAbstractItemView {{ background: {PANEL_2}; selection-background-color: #3a3150; }}
QTextBrowser {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px; }}
QFrame#logCard {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QPushButton#modeBtn {{ padding: 3px 12px; }}
QPushButton#modeBtn:checked {{ background: #3a3150; border-color: {ACCENT}; }}
QPushButton#rowDelete {{
    padding: 0; min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
    border: none; background: transparent; color: {TEXT_DIM};
}}
QPushButton#rowDelete:hover {{ color: #e07070; background: transparent; }}
QListWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; outline: none; }}
QListWidget::item {{ padding: 8px 10px; }}
QListWidget::item:selected {{ background: #3a3150; color: {TEXT}; }}
QSpinBox {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 8px; }}
QScrollArea#logList, QWidget#logViewport, QWidget#logListBody {{ background: transparent; border: none; }}
QFrame#logCard QLabel {{ background: transparent; }}
QFrame#pullRow {{ background: transparent; border: none; border-radius: 6px; }}
QFrame#pullRow[selected="true"] {{ background: #3a3150; }}
QFrame#stackPanel {{ background: rgba(14, 15, 19, 217); border: 1px solid {BORDER}; border-radius: 8px; }}
QWidget#stackColumn {{ background: transparent; border: none; }}
QFrame#stackCard {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#stackCard[lifted="true"] {{ background: #262b38; border: 1px solid {ACCENT}; }}
QFrame#stackPanel QLabel {{ background: transparent; }}
QLabel#stackTitle {{ color: {TEXT_DIM}; }}
QWidget#raidGrid {{ background: transparent; border: none; }}
QSplitter::handle {{ background: {BG}; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QProgressBar {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QLineEdit {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QLabel#title {{ font-size: 10pt; color: {TEXT_DIM}; padding: 2px 6px; }}
QLabel#aboutName {{ font-size: 13pt; }}
QLabel#aboutHeading {{ color: {TEXT_DIM}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QFrame#auraPopup {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame#auraPopup QCheckBox {{ background: transparent; spacing: 8px; padding: 2px 4px; }}
QFrame#auraPopup QLabel {{ background: transparent; color: {TEXT_DIM}; }}
QFrame#auraPopup QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid {TEXT_DIM}; border-radius: 3px; background: {BG};
}}
QFrame#auraPopup QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
"""
