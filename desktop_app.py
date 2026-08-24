from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services.extrator import NOME_DO_GRUPO, extrair_dados_comunidade
from services.storage import export_to_csv, fetch_message_by_id, fetch_recent, get_db_path, init_db

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EXPORT_DIR = BASE_DIR / "data"
DEFAULT_EXPORT_PATH = DEFAULT_EXPORT_DIR / "export_messages.csv"


class ExtractionWorker(QObject):
    finished = Signal(str)
    status = Signal(str)

    def __init__(self, grupo: str, comunidade: str, tipo_filtro: str, valor: int):
        super().__init__()
        self.grupo = grupo
        self.comunidade = comunidade
        self.tipo_filtro = tipo_filtro
        self.valor = valor

    @Slot()
    def run(self):
        try:
            nome_grupo = self.grupo.strip() or NOME_DO_GRUPO
            nome_comunidade = self.comunidade.strip() or ""

            kwargs = {
                "nome_grupo": nome_grupo,
                "nome_comunidade": nome_comunidade,
                "tipo_filtro": self.tipo_filtro,
            }

            if self.tipo_filtro == "mes":
                kwargs["meses"] = int(self.valor)
            else:
                kwargs["dias"] = int(self.valor)

            self.status.emit(f"Iniciando coleta por {self.tipo_filtro} com valor {self.valor}...")
            extrair_dados_comunidade(**kwargs)
            self.status.emit("Coleta concluída com sucesso.")
            self.finished.emit("OK")
        except Exception as exc:  # pragma: no cover - runtime UI path
            self.status.emit(f"Erro na coleta: {exc}")
            self.finished.emit(f"ERRO: {exc}")


class WhatsAppInsightsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WhatsApp Insights")
        self.resize(1100, 760)
        self.thread = None
        self.worker = None
        self.db_path = get_db_path()
        init_db(self.db_path)
        self._build_ui()
        self._refresh_table()

    def _build_ui(self):
        central_widget = QWidget()
        central_widget.setStyleSheet(
            """
            QWidget { background: #0f172a; color: #e2e8f0; }
            QGroupBox { border: 1px solid #334155; border-radius: 8px; margin-top: 12px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #93c5fd; }
            QLabel { color: #dbeafe; }
            QLineEdit, QComboBox, QSpinBox, QTextEdit, QTableWidget, QListWidget { background: #111827; border: 1px solid #334155; border-radius: 6px; color: #f8fafc; }
            QPushButton { background: #2563eb; color: white; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #1d4ed8; }
            QPushButton#secondary { background: #1f2937; }
            QPushButton#danger { background: #b91c1c; }
            QTableWidget { gridline-color: #334155; }
            QListWidget { outline: none; border: 1px solid #1e293b; }
            QListWidget::item { padding: 12px 10px; border-radius: 6px; }
            QListWidget::item:selected { background: #1d4ed8; color: white; }
            """
        )

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(12)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.addItem(QListWidgetItem("Resumo"))
        self.sidebar.addItem(QListWidgetItem("Mensagens"))
        self.sidebar.addItem(QListWidgetItem("Detalhes"))
        self.sidebar.addItem(QListWidgetItem("Banco de Dados"))
        self.sidebar.addItem(QListWidgetItem("Exportação"))
        self.sidebar.setCurrentRow(1)

        self.stacked = QStackedWidget()
        self.dashboard_page = self._build_dashboard_page()
        self.messages_page = self._build_messages_page()
        self.detail_page = self._build_detail_page()
        self.database_page = self._build_database_page()
        self.export_page = self._build_export_page()

        self.stacked.addWidget(self.dashboard_page)
        self.stacked.addWidget(self.messages_page)
        self.stacked.addWidget(self.detail_page)
        self.stacked.addWidget(self.database_page)
        self.stacked.addWidget(self.export_page)

        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.stacked)

        self.setCentralWidget(central_widget)
        self.sidebar.currentRowChanged.connect(self.stacked.setCurrentIndex)

        self._append_log("Interface inicializada.")

    def _build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)

        title = QLabel("Resumo do sistema")
        title.setStyleSheet("font-size: 22px; font-weight: 600; color: #bfdbfe;")

        summary = QTextEdit()
        summary.setReadOnly(True)
        summary.setHtml(
            """
            <html><body>
            <p><b>WhatsApp Insights</b></p>
            <p>O sistema salva as mensagens em um banco local SQLite para consulta rápida e análise.</p>
            <p><b>Banco atual:</b> {db_path}</p>
            <p><b>Fluxo:</b> coletar mensagens → salvar no banco → consultar na tabela → exportar para CSV/JSON.</p>
            </body></html>
            """.format(db_path=self.db_path)
        )
        summary.setMinimumHeight(180)

        layout.addWidget(title)
        layout.addWidget(summary)
        return page

    def _build_messages_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        section = QGroupBox("Parâmetros da coleta")
        form_layout = QFormLayout(section)

        self.input_grupo = QComboBox()
        self.input_grupo.setEditable(True)
        self.input_grupo.addItem(NOME_DO_GRUPO)
        self.input_grupo.setEditText(NOME_DO_GRUPO)

        self.input_comunidade = QComboBox()
        self.input_comunidade.setEditable(True)
        self.input_comunidade.addItem("")
        self.input_comunidade.setEditText("")

        self.tipo_filtro_combo = QComboBox()
        self.tipo_filtro_combo.addItems(["Por mês", "Por dias"])

        self.valor_spin = QSpinBox()
        self.valor_spin.setMinimum(1)
        self.valor_spin.setMaximum(120)
        self.valor_spin.setValue(1)

        form_layout.addRow("Grupo:", self.input_grupo)
        form_layout.addRow("Comunidade:", self.input_comunidade)
        form_layout.addRow("Filtro:", self.tipo_filtro_combo)
        form_layout.addRow("Valor:", self.valor_spin)

        top_buttons = QHBoxLayout()
        self.btn_coletar = QPushButton("Coletar mensagens")
        self.btn_exportar = QPushButton("Exportar CSV")
        self.btn_atualizar = QPushButton("Atualizar tabela")
        self.btn_exportar.setObjectName("secondary")
        self.btn_atualizar.setObjectName("secondary")

        top_buttons.addWidget(self.btn_coletar)
        top_buttons.addWidget(self.btn_exportar)
        top_buttons.addWidget(self.btn_atualizar)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Log da aplicação...")
        self.log_output.setMinimumHeight(170)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Data/Hora", "Remetente", "Texto", "Anexos", "ID"])
        self.table.setAlternatingRowColors(True)

        layout.addWidget(section)
        layout.addLayout(top_buttons)
        layout.addWidget(self.log_output)
        layout.addWidget(self.table)

        self.btn_coletar.clicked.connect(self._on_coletar)
        self.btn_exportar.clicked.connect(self._on_exportar)
        self.btn_atualizar.clicked.connect(self._refresh_table)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.cellDoubleClicked.connect(self._open_message_detail)

        return page

    def _build_detail_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        header = QHBoxLayout()
        self.detail_title = QLabel("Detalhes da mensagem")
        self.detail_title.setStyleSheet("font-size: 22px; font-weight: 600; color: #bfdbfe;")
        self.btn_voltar_mensagens = QPushButton("Voltar para mensagens")
        self.btn_voltar_mensagens.setObjectName("secondary")
        header.addWidget(self.detail_title)
        header.addStretch()
        header.addWidget(self.btn_voltar_mensagens)

        details_group = QGroupBox("Informações gerais")
        details_layout = QFormLayout(details_group)
        self.detail_id = QLabel("-")
        self.detail_data = QLabel("-")
        self.detail_remetente = QLabel("-")
        self.detail_anexos = QLabel("-")
        self.detail_grupo = QLabel("-")
        self.detail_comunidade = QLabel("-")
        details_layout.addRow("ID:", self.detail_id)
        details_layout.addRow("Data/Hora:", self.detail_data)
        details_layout.addRow("Remetente:", self.detail_remetente)
        details_layout.addRow("Anexos:", self.detail_anexos)
        details_layout.addRow("Grupo:", self.detail_grupo)
        details_layout.addRow("Comunidade:", self.detail_comunidade)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setPlaceholderText("Texto da mensagem...")

        self.detail_metadata = QTextEdit()
        self.detail_metadata.setReadOnly(True)
        self.detail_metadata.setPlaceholderText("Metadados extras...")
        self.detail_metadata.setMinimumHeight(180)

        layout.addLayout(header)
        layout.addWidget(details_group)
        layout.addWidget(QLabel("Texto completo:"))
        layout.addWidget(self.detail_text)
        layout.addWidget(QLabel("Metadados extras:"))
        layout.addWidget(self.detail_metadata)

        self.btn_voltar_mensagens.clicked.connect(lambda: self.sidebar.setCurrentRow(1))
        return page

    def _build_database_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        self.db_group = QGroupBox("Banco de dados local")
        self.db_group.setStyleSheet("QGroupBox { border: 1px solid #334155; border-radius: 8px; margin-top: 10px; padding-top: 12px; }")
        db_layout = QVBoxLayout(self.db_group)

        self.db_label = QLabel("Arquivo salvo localmente para consulta e análise rápida.")
        self.db_label.setWordWrap(True)

        db_path_layout = QHBoxLayout()
        self.db_path_input = QLineEdit(self.db_path)
        self.db_path_input.setReadOnly(True)
        self.db_path_input.setToolTip(self.db_path)
        db_path_layout.addWidget(self.db_path_input)

        self.btn_abre_db = QPushButton("Abrir banco")
        self.btn_abrir_pasta_db = QPushButton("Abrir pasta")
        self.btn_copiar_caminho_db = QPushButton("Copiar caminho")
        self.btn_abre_db.setObjectName("secondary")
        self.btn_abrir_pasta_db.setObjectName("secondary")
        self.btn_copiar_caminho_db.setObjectName("secondary")

        db_path_layout.addWidget(self.btn_abre_db)
        db_path_layout.addWidget(self.btn_abrir_pasta_db)
        db_path_layout.addWidget(self.btn_copiar_caminho_db)

        db_layout.addWidget(self.db_label)
        db_layout.addLayout(db_path_layout)

        layout.addWidget(self.db_group)
        self.btn_abre_db.clicked.connect(self._on_abrir_db)
        self.btn_abrir_pasta_db.clicked.connect(self._on_abrir_pasta_db)
        self.btn_copiar_caminho_db.clicked.connect(self._on_copiar_caminho_db)

        return page

    def _build_export_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        export_group = QGroupBox("Exportação do banco")
        export_layout = QVBoxLayout(export_group)

        self.export_path_input = QLineEdit(str(DEFAULT_EXPORT_PATH))
        self.btn_selecionar_pasta = QPushButton("Selecionar pasta")
        self.btn_selecionar_pasta.setObjectName("secondary")
        self.btn_exportar_pagina = QPushButton("Exportar CSV")

        row = QHBoxLayout()
        row.addWidget(QLabel("Destino CSV:"))
        row.addWidget(self.export_path_input)
        row.addWidget(self.btn_selecionar_pasta)
        export_layout.addLayout(row)
        export_layout.addWidget(self.btn_exportar_pagina)

        layout.addWidget(export_group)

        self.btn_selecionar_pasta.clicked.connect(self._on_selecionar_pasta)
        self.btn_exportar_pagina.clicked.connect(self._on_exportar)
        return page

    def _append_log(self, message: str):
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.log_output.append(f"[{timestamp}] {message}")

    def _set_busy(self, busy: bool):
        if hasattr(self, "btn_coletar"):
            self.btn_coletar.setEnabled(not busy)
        if hasattr(self, "btn_exportar"):
            self.btn_exportar.setEnabled(not busy)
        if hasattr(self, "btn_atualizar"):
            self.btn_atualizar.setEnabled(not busy)
        if hasattr(self, "btn_abre_db"):
            self.btn_abre_db.setEnabled(not busy)
        if hasattr(self, "btn_abrir_pasta_db"):
            self.btn_abrir_pasta_db.setEnabled(not busy)
        if hasattr(self, "btn_copiar_caminho_db"):
            self.btn_copiar_caminho_db.setEnabled(not busy)
        if hasattr(self, "btn_selecionar_pasta"):
            self.btn_selecionar_pasta.setEnabled(not busy)
        if hasattr(self, "btn_exportar_pagina"):
            self.btn_exportar_pagina.setEnabled(not busy)

    def _get_filtro(self):
        tipo = self.tipo_filtro_combo.currentText()
        valor = int(self.valor_spin.value())
        if tipo == "Por mês":
            return "mes", valor
        return "dias", valor

    def _on_coletar(self):
        grupo = self.input_grupo.currentText().strip()
        comunidade = self.input_comunidade.currentText().strip()
        tipo_filtro, valor = self._get_filtro()

        self._set_busy(True)
        self._append_log(f"Iniciando coleta com filtro {tipo_filtro} e valor {valor}.")

        self.thread = QThread()
        self.worker = ExtractionWorker(grupo, comunidade, tipo_filtro, valor)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self._append_log)
        self.worker.finished.connect(self._on_coleta_finalizada)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        self.thread.start()

    def _on_coleta_finalizada(self, result: str):
        self._set_busy(False)
        if result.startswith("ERRO"):
            self._append_log("Coleta falhou.")
            QMessageBox.critical(self, "Erro na coleta", result.replace("ERRO: ", ""))
            return

        self._append_log("Coleta concluída com sucesso.")
        self._refresh_table()

    def _on_selecionar_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self,
            "Selecionar pasta para salvar o CSV",
            str(DEFAULT_EXPORT_DIR),
        )
        if not pasta:
            return

        nome_arquivo_csv = os.path.join(pasta, "export_messages.csv")
        self.export_path_input.setText(nome_arquivo_csv)
        self._append_log(f"Pasta de exportação definida em: {pasta}")

    def _on_exportar(self):
        destino = self.export_path_input.text().strip()
        if not destino:
            destino = str(DEFAULT_EXPORT_PATH)

        pasta_destino = os.path.dirname(destino)
        if pasta_destino:
            os.makedirs(pasta_destino, exist_ok=True)

        try:
            export_to_csv(destino, db_path=get_db_path())
            self._append_log(f"CSV exportado em: {destino}")
            QMessageBox.information(self, "Exportação concluída", f"Arquivo salvo em:\n{destino}")
        except Exception as exc:  # pragma: no cover - runtime UI path
            self._append_log(f"Erro ao exportar CSV: {exc}")
            QMessageBox.critical(self, "Erro ao exportar", str(exc))

    def _on_abrir_db(self):
        try:
            if os.path.exists(self.db_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(self.db_path))
                self._append_log(f"Banco aberto: {self.db_path}")
            else:
                pasta_db = os.path.dirname(self.db_path)
                os.makedirs(pasta_db, exist_ok=True)
                QDesktopServices.openUrl(QUrl.fromLocalFile(pasta_db))
                self._append_log(f"Pasta do banco aberta: {pasta_db}")
        except Exception as exc:  # pragma: no cover - runtime UI path
            self._append_log(f"Erro ao abrir banco: {exc}")
            QMessageBox.critical(self, "Erro ao abrir banco", str(exc))

    def _on_abrir_pasta_db(self):
        try:
            pasta_db = os.path.dirname(self.db_path)
            os.makedirs(pasta_db, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(pasta_db))
            self._append_log(f"Pasta do banco aberta: {pasta_db}")
        except Exception as exc:  # pragma: no cover - runtime UI path
            self._append_log(f"Erro ao abrir pasta do banco: {exc}")
            QMessageBox.critical(self, "Erro ao abrir pasta", str(exc))

    def _on_copiar_caminho_db(self):
        try:
            QApplication.clipboard().setText(self.db_path)
            self._append_log(f"Caminho do banco copiado: {self.db_path}")
            QMessageBox.information(self, "Caminho copiado", f"Caminho do banco copiado para a área de transferência:\n{self.db_path}")
        except Exception as exc:  # pragma: no cover - runtime UI path
            self._append_log(f"Erro ao copiar caminho do banco: {exc}")
            QMessageBox.critical(self, "Erro ao copiar caminho", str(exc))

    def _render_message_detail(self, data: dict | None):
        if data is None:
            self.detail_id.setText("-")
            self.detail_data.setText("-")
            self.detail_remetente.setText("-")
            self.detail_anexos.setText("-")
            self.detail_grupo.setText("-")
            self.detail_comunidade.setText("-")
            self.detail_text.setPlainText("Nenhuma mensagem selecionada.")
            self.detail_metadata.setPlainText("")
            return

        self.detail_id.setText(str(data.get("id") or "-"))
        self.detail_data.setText(str(data.get("data_hora") or "-"))
        self.detail_remetente.setText(str(data.get("remetente") or "-"))
        self.detail_anexos.setText("Sim" if data.get("has_attachments") else "Não")
        self.detail_grupo.setText(str(data.get("grupo_nome") or "-"))
        self.detail_comunidade.setText(str(data.get("comunidade_nome") or "-"))
        self.detail_text.setPlainText(str(data.get("texto") or data.get("transcript") or "Mensagem sem texto."))

        extra = {
            "coleta_id": data.get("coleta_id"),
            "coletado_em": data.get("coletado_em"),
            "meses_back": data.get("meses_back"),
            "semanas_back": data.get("semanas_back"),
            "is_reply": bool(data.get("is_reply")),
            "reply_author": data.get("reply_author"),
            "reply_text": data.get("reply_text"),
            "texto_normalizado": data.get("texto_normalizado"),
            "attachments_json": data.get("attachments_json"),
            "reactions_json": data.get("reactions_json"),
            "topics_json": data.get("topics_json"),
            "created_at": data.get("created_at"),
        }
        self.detail_metadata.setPlainText(str(extra))

    def _open_message_detail(self, row: int, column: int):
        item_id = self.table.item(row, 4)
        if item_id is None:
            return

        message_id = item_id.text()
        payload = fetch_message_by_id(message_id, db_path=self.db_path)
        self._render_message_detail(payload)
        self.sidebar.setCurrentRow(2)

    def _refresh_table(self):
        try:
            init_db(self.db_path)
            registros = fetch_recent(limit=100, db_path=self.db_path)
            self.table.setRowCount(len(registros))
            self.table.setColumnCount(5)
            self.table.setHorizontalHeaderLabels(["Data/Hora", "Remetente", "Texto", "Anexos", "ID"])

            for linha_idx, item in enumerate(registros):
                self.table.setItem(linha_idx, 0, QTableWidgetItem(str(item.get("data_hora", ""))))
                self.table.setItem(linha_idx, 1, QTableWidgetItem(str(item.get("remetente", ""))))
                self.table.setItem(linha_idx, 2, QTableWidgetItem(str(item.get("texto", ""))[:200]))
                self.table.setItem(linha_idx, 3, QTableWidgetItem("Sim" if item.get("has_attachments") else "Não"))
                self.table.setItem(linha_idx, 4, QTableWidgetItem(str(item.get("id", ""))))

            self.table.resizeColumnsToContents()
        except Exception as exc:  # pragma: no cover - runtime UI path
            self._append_log(f"Erro ao atualizar tabela: {exc}")


def launch_app():
    app = QApplication.instance() or QApplication(sys.argv)
    window = WhatsAppInsightsWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    launch_app()
