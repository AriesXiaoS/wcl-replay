# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..sources.wcl_api.urls import normalize_host
from ..workers import test_credentials_job
from .loader import TaskRunner

HOSTS = ("www.warcraftlogs.com", "cn.warcraftlogs.com")


class CredentialsDialog(QDialog):
    def __init__(
        self, settings: QSettings, parent: QWidget | None = None, *, runner: TaskRunner | None = None
    ):
        super().__init__(parent)
        self.settings = settings
        self.tasks = runner or TaskRunner(self)
        self._owns_runner = runner is None
        self._test_generation = 0
        self.finished.connect(self._finished)
        self.setWindowTitle("WCL API 设置")
        self.setMinimumWidth(460)

        info = QLabel(
            '在 <a href="https://www.warcraftlogs.com/api/clients/">warcraftlogs.com/api/clients</a> '
            "新建一个 API Client。<br>Redirect URL 填 http://localhost 即可，"
            "然后把 Client ID 和 Client Secret 填到这里。"
        )
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)

        self.client_id = QLineEdit(str(settings.value("wcl_client_id", "")))
        self.secret = QLineEdit(str(settings.value("wcl_client_secret", "")))
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.addItems(HOSTS)
        self.host.setCurrentText(str(settings.value("wcl_host", HOSTS[0])))

        form = QFormLayout()
        form.addRow("Client ID", self.client_id)
        form.addRow("Client Secret", self.secret)
        form.addRow("API 域名", self.host)

        test = self.test_btn = QPushButton("测试连接")
        test.clicked.connect(self._test)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.addButton(test, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _values(self) -> tuple[str, str, str]:
        return (
            self.client_id.text().strip(),
            self.secret.text().strip(),
            self.host.currentText().strip() or HOSTS[0],
        )

    def _test(self) -> None:
        cid, sec, host = self._values()
        try:
            host = normalize_host(host)
            if not cid or not sec:
                raise ValueError("Client ID 和 Client Secret 都需要填写。")
        except ValueError as exc:
            QMessageBox.warning(self, "连接失败", str(exc))
            return
        self._test_generation += 1
        generation = self._test_generation
        self.test_btn.setEnabled(False)
        self.test_btn.setText("正在测试…")
        self._test_job = self.tasks.run(
            test_credentials_job,
            (cid, sec, host),
            lambda _result: self._tested(generation),
            lambda message: self._tested(generation, message),
        )

    def _tested(self, generation: int, error: str | None = None) -> None:
        if generation != self._test_generation:
            return
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试连接")
        if error:
            QMessageBox.warning(self, "连接失败", error.strip().splitlines()[-1])
        else:
            QMessageBox.information(self, "连接成功", "已成功获取 WCL API 访问令牌。")

    def _finished(self, _result: int) -> None:
        self._test_generation += 1
        handle = getattr(self, "_test_job", None)
        if handle is not None:
            handle.cancel()
            self._test_job = None
        if self._owns_runner:
            self.tasks.shutdown()

    def _save(self) -> None:
        cid, sec, host = self._values()
        try:
            host = normalize_host(host)
        except ValueError as exc:
            QMessageBox.warning(self, "WCL API 设置", str(exc))
            return
        if not cid or not sec:
            QMessageBox.warning(self, "WCL API 设置", "Client ID 和 Client Secret 都需要填写。")
            return
        self.settings.setValue("wcl_client_id", cid)
        self.settings.setValue("wcl_client_secret", sec)
        self.settings.setValue("wcl_host", host)
        self.accept()


class ReportDialog(QDialog):
    def __init__(self, default_url: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("打开 WCL 报告")
        self.setMinimumWidth(560)
        self.edit = QLineEdit(str(default_url or ""))
        self.edit.setPlaceholderText("https://cn.warcraftlogs.com/reports/XXXXXXXXXXXXXXXX?fight=32")
        hint = QLabel("粘贴报告链接（带 ?fight= 会直接打开该场战斗），或只填 16 位报告代码。")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self.edit)
        lay.addWidget(buttons)

    def _ok(self) -> None:
        from ..sources.wcl_api import parse_report_url

        try:
            parse_report_url(self.url())
        except ValueError as exc:
            QMessageBox.warning(self, "链接无效", str(exc))
            return
        self.accept()

    def url(self) -> str:
        return self.edit.text().strip()
