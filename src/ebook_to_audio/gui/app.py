"""PyQt5 图形界面：选择 txt 文件，调用核心模块转换为 mp3 / m4b。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ebook_to_audio.core.audio_export import INSTALL_HINT, ffmpeg_available
from ebook_to_audio.core.converter import (
    ConversionResult,
    convert_text_to_formats,
    output_paths,
)
from ebook_to_audio.core.epub_converter import convert_epub_file
from ebook_to_audio.core.epub_processor import inspect_epub
from ebook_to_audio.core.tts_processor import (
    DEFAULT_CONCURRENCY,
    DEFAULT_VOICE,
    ConversionCancelled,
    synthesize_chunk,
)
from ebook_to_audio.gui.icon import set_window_icon
from ebook_to_audio.utils.file_handler import get_output_filename, read_file

# 常用音色；键为 edge-tts 的 voice id
VOICE_OPTIONS = [
    # 中文普通话
    ("zh-CN-XiaoxiaoNeural", "中文 - 晓晓 (女)"),
    ("zh-CN-XiaoyiNeural", "中文 - 小伊 (女)"),
    ("zh-CN-YunjianNeural", "中文 - 云健 (男)"),
    ("zh-CN-YunxiNeural", "中文 - 云希 (男)"),
    ("zh-CN-YunxiaNeural", "中文 - 云夏 (男)"),
    ("zh-CN-YunyangNeural", "中文 - 云阳 (男)"),
    # 英文
    ("en-US-AriaNeural", "英文 (US) - Aria"),
    ("en-US-GuyNeural", "英文 (US) - Guy"),
    ("en-GB-LibbyNeural", "英文 (GB) - Libby"),
    ("en-GB-RyanNeural", "英文 (GB) - Ryan"),
    ("en-AU-NatashaNeural", "英文 (AU) - Natasha"),
    ("en-AU-WilliamNeural", "英文 (AU) - William"),
]


def strip_audio_extension(path: str) -> str:
    """去掉 .mp3/.m4b 后缀，得到输出主名。"""
    lower = path.lower()
    for extension in (".mp3", ".m4b"):
        if lower.endswith(extension):
            return path[: -len(extension)]
    return path


def is_epub(path: str | None) -> bool:
    return bool(path and path.lower().endswith(".epub"))


class ConvertThread(QThread):
    """后台转化线程；只调用核心模块，不重复实现转换逻辑。"""

    progress_updated = pyqtSignal(int)
    # 避免覆盖 QThread 自带的 finished 信号
    conversion_finished = pyqtSignal(object)
    conversion_cancelled = pyqtSignal()
    conversion_error = pyqtSignal(str)

    def __init__(
        self,
        input_file: str,
        output_file: str,
        voice: str,
        output_format: str,
        workers: int,
        rate: str = "+0%",
    ):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.voice = voice
        self.output_format = output_format
        self.workers = workers
        self.rate = rate
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            if is_epub(self.input_file):
                result: ConversionResult = convert_epub_file(
                    self.input_file,
                    self.output_file,
                    self.voice,
                    output_format=self.output_format,
                    rate=self.rate,
                    progress=self.progress_updated.emit,
                    cancel_check=self._cancel_event.is_set,
                    max_concurrency=self.workers,
                )
            else:
                result = convert_text_to_formats(
                    self.input_file,
                    self.output_file,
                    self.voice,
                    output_format=self.output_format,
                    rate=self.rate,
                    progress=self.progress_updated.emit,
                    cancel_check=self._cancel_event.is_set,
                    max_concurrency=self.workers,
                )
        except ConversionCancelled:
            self.conversion_cancelled.emit()
            return
        except Exception as exc:
            self.conversion_error.emit(f"{type(exc).__name__}: {exc}")
            return
        self.conversion_finished.emit(result)


class PreviewThread(QThread):
    """后台试听线程：合成一句示例语音并交给系统播放器。"""

    preview_finished = pyqtSignal()
    preview_error = pyqtSignal(str)

    def __init__(self, text: str, voice: str):
        super().__init__()
        self.text = text
        self.voice = voice

    def run(self) -> None:
        temp_file = None
        try:
            audio = asyncio.run(synthesize_chunk(self.text, self.voice))
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
                temp_file = temp.name
                temp.write(audio)

            if sys.platform == "darwin":  # macOS
                subprocess.run(["afplay", temp_file])
            elif sys.platform == "win32":  # Windows
                os.startfile(temp_file)  # type: ignore[attr-defined]
            else:  # Linux
                subprocess.run(["xdg-open", temp_file])
        except Exception as exc:
            self.preview_error.emit(f"{type(exc).__name__}: {exc}")
            return
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except OSError:
                    pass
        self.preview_finished.emit()


class TextToAudioGUI(QMainWindow):
    """文本转音频 GUI 应用程序。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("文本转音频工具")
        self.setGeometry(100, 100, 640, 560)

        set_window_icon(self)

        self.input_file: str | None = None
        self.text_content = ""
        self.convert_thread: ConvertThread | None = None
        self.preview_thread: PreviewThread | None = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        self.create_file_input_area(main_layout)
        self.create_voice_selection(main_layout)
        self.create_convert_button(main_layout)
        self.create_progress_display(main_layout)

        self.setAcceptDrops(True)

    # ---------- 界面构建 ----------

    def create_file_input_area(self, parent_layout: QVBoxLayout) -> None:
        file_frame = QFrame()
        file_frame.setFrameShape(QFrame.StyledPanel)
        file_frame.setFrameShadow(QFrame.Raised)
        file_layout = QVBoxLayout(file_frame)

        title_label = QLabel("文件选择")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        file_layout.addWidget(title_label)

        self.drop_area = QLabel("拖放文本文件到此处，或点击选择文件")
        self.drop_area.setAlignment(Qt.AlignCenter)
        self.drop_area.setMinimumHeight(140)
        self.drop_area.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; border-radius: 5px; "
            "background-color: #f9f9f9; padding: 20px; }"
        )
        self.drop_area.setFont(QFont("Arial", 12))
        self.drop_area.mousePressEvent = self.on_drop_area_click
        file_layout.addWidget(self.drop_area)

        self.file_info_label = QLabel("")
        self.file_info_label.setFont(QFont("Arial", 10))
        file_layout.addWidget(self.file_info_label)

        output_layout = QHBoxLayout()
        output_label = QLabel("输出文件:")
        output_label.setFont(QFont("Arial", 10))
        output_layout.addWidget(output_label)

        self.output_edit = QLineEdit()
        self.output_edit.setFont(QFont("Arial", 10))
        self.output_edit.setPlaceholderText("输出文件主名，格式自动添加 .mp3/.m4b")
        output_layout.addWidget(self.output_edit, 1)

        self.output_button = QPushButton("浏览…")
        self.output_button.setFont(QFont("Arial", 10))
        self.output_button.clicked.connect(self.on_browse_output)
        output_layout.addWidget(self.output_button)

        file_layout.addLayout(output_layout)

        format_layout = QHBoxLayout()
        format_label = QLabel("输出格式:")
        format_label.setFont(QFont("Arial", 10))
        format_layout.addWidget(format_label)

        self.format_combo = QComboBox()
        self.format_combo.setFont(QFont("Arial", 10))
        self.format_combo.addItem("M4B（需要 ffmpeg）", "m4b")
        self.format_combo.addItem("MP3", "mp3")
        self.format_combo.addItem("MP3 + M4B", "both")
        format_layout.addWidget(self.format_combo, 1)
        file_layout.addLayout(format_layout)

        parent_layout.addWidget(file_frame)

    def create_voice_selection(self, parent_layout: QVBoxLayout) -> None:
        voice_frame = QFrame()
        voice_frame.setFrameShape(QFrame.StyledPanel)
        voice_frame.setFrameShadow(QFrame.Raised)
        voice_layout = QVBoxLayout(voice_frame)

        title_label = QLabel("语音选择")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        voice_layout.addWidget(title_label)

        combo_layout = QHBoxLayout()

        voice_label = QLabel("播音员:")
        voice_label.setFont(QFont("Arial", 10))
        combo_layout.addWidget(voice_label)

        self.voice_combo = QComboBox()
        for voice_id, voice_name in VOICE_OPTIONS:
            self.voice_combo.addItem(voice_name, voice_id)
        default_voice_index = self.voice_combo.findData(DEFAULT_VOICE)
        if default_voice_index >= 0:
            self.voice_combo.setCurrentIndex(default_voice_index)
        self.voice_combo.setFont(QFont("Arial", 10))
        combo_layout.addWidget(self.voice_combo, 1)

        self.preview_button = QPushButton("试听")
        self.preview_button.setFont(QFont("Arial", 10))
        self.preview_button.setMinimumWidth(60)
        self.preview_button.clicked.connect(self.on_preview_button_click)
        combo_layout.addWidget(self.preview_button)

        voice_layout.addLayout(combo_layout)

        workers_layout = QHBoxLayout()
        workers_label = QLabel("并发请求:")
        workers_label.setFont(QFont("Arial", 10))
        workers_layout.addWidget(workers_label)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        self.workers_spin.setValue(DEFAULT_CONCURRENCY)
        self.workers_spin.setToolTip("同时请求 Edge TTS 的块数；过高可能被服务端限流")
        self.workers_spin.setFont(QFont("Arial", 10))
        workers_layout.addWidget(self.workers_spin)
        workers_layout.addStretch(1)
        voice_layout.addLayout(workers_layout)

        parent_layout.addWidget(voice_frame)

    def create_convert_button(self, parent_layout: QVBoxLayout) -> None:
        button_layout = QHBoxLayout()

        self.convert_button = QPushButton("开始转化")
        self.convert_button.setFont(QFont("Arial", 12))
        self.convert_button.setMinimumHeight(40)
        self.convert_button.clicked.connect(self.on_convert_button_click)
        button_layout.addWidget(self.convert_button, 3)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFont(QFont("Arial", 12))
        self.cancel_button.setMinimumHeight(40)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.on_cancel_button_click)
        button_layout.addWidget(self.cancel_button, 1)

        parent_layout.addLayout(button_layout)

    def create_progress_display(self, parent_layout: QVBoxLayout) -> None:
        progress_frame = QFrame()
        progress_frame.setFrameShape(QFrame.StyledPanel)
        progress_frame.setFrameShadow(QFrame.Raised)
        progress_layout = QVBoxLayout(progress_frame)

        title_label = QLabel("转化进度")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        progress_layout.addWidget(title_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("0%")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setFont(QFont("Arial", 10))
        progress_layout.addWidget(self.progress_label)

        parent_layout.addWidget(progress_frame)

    # ---------- 拖放 ----------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_area.setStyleSheet(
                "QLabel { border: 2px dashed #6666ff; border-radius: 5px; "
                "background-color: #f0f0ff; padding: 20px; }"
            )

    def dragLeaveEvent(self, event) -> None:
        self._reset_drop_area_style()

    def dropEvent(self, event: QDropEvent) -> None:
        self._reset_drop_area_style()
        if self.convert_thread and self.convert_thread.isRunning():
            return
        if not event.mimeData().hasUrls():
            return

        file_path = event.mimeData().urls()[0].toLocalFile()
        if not file_path:
            return
        if not file_path.lower().endswith((".txt", ".epub")):
            self.file_info_label.setText("只支持 .txt 或 .epub 文件")
            return
        if not os.path.exists(file_path):
            self.file_info_label.setText("文件不存在")
            return

        self.input_file = file_path
        self.update_file_info()

    def _reset_drop_area_style(self) -> None:
        self.drop_area.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; border-radius: 5px; "
            "background-color: #f9f9f9; padding: 20px; }"
        )

    def on_drop_area_click(self, event) -> None:
        if self.convert_thread and self.convert_thread.isRunning():
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文本或电子书文件",
            "",
            "文本/电子书 (*.txt *.epub);;所有文件 (*.*)",
        )
        if file_path:
            self.input_file = file_path
            self.update_file_info()

    def on_browse_output(self) -> None:
        output_format = self.format_combo.itemData(self.format_combo.currentIndex())
        if self.output_edit.text().strip():
            default = strip_audio_extension(self.output_edit.text().strip())
        elif self.input_file:
            default = strip_audio_extension(get_output_filename(self.input_file))
        else:
            default = ""

        if output_format == "mp3":
            default += ".mp3"
            file_filter = "MP3 文件 (*.mp3)"
        elif output_format == "m4b":
            default += ".m4b"
            file_filter = "M4B 文件 (*.m4b)"
        else:
            file_filter = "音频文件 (*.mp3 *.m4b)"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "选择输出文件", default, file_filter
        )
        if file_path:
            self.output_edit.setText(strip_audio_extension(file_path))

    # ---------- 文件信息 ----------

    def update_file_info(self) -> None:
        if not self.input_file:
            return
        file_name = os.path.basename(self.input_file)

        if is_epub(self.input_file):
            try:
                meta = inspect_epub(self.input_file)
            except Exception as exc:
                self.file_info_label.setText(f"错误: {exc}")
                return
            self.text_content = ""
            info = f"文件: {file_name} | {meta.title}"
            if meta.author:
                info += f" | {meta.author}"
            info += f" | 章节: {meta.chapter_count}"
            self.file_info_label.setText(info)
            self.drop_area.setText(f"已选择文件: {file_name}")
            self.output_edit.setText(
                strip_audio_extension(get_output_filename(self.input_file))
            )
            return

        try:
            self.text_content = read_file(self.input_file)
        except Exception as exc:
            self.file_info_label.setText(f"错误: {exc}")
            return

        text = self.text_content
        chinese_chars = sum(1 for char in text if "一" <= char <= "鿿")
        words = text.split()
        word_count = sum(
            1 for word in words if any("a" <= char.lower() <= "z" for char in word)
        )

        if chinese_chars > 0:
            self.file_info_label.setText(
                f"文件: {file_name} | 中文字符: {chinese_chars} | 英文单词: {word_count}"
            )
        else:
            self.file_info_label.setText(f"文件: {file_name} | 英文单词: {word_count}")

        self.drop_area.setText(f"已选择文件: {file_name}")
        self.output_edit.setText(strip_audio_extension(get_output_filename(self.input_file)))

    # ---------- 转化 ----------

    def on_convert_button_click(self) -> None:
        if not self.input_file:
            QMessageBox.warning(self, "警告", "请先选择一个 txt 或 epub 文件")
            return
        epub_input = is_epub(self.input_file)
        if not epub_input and (not self.text_content or not self.text_content.strip()):
            QMessageBox.warning(self, "警告", "文本内容为空，请选择包含内容的文本文件")
            return

        output_format = self.format_combo.itemData(self.format_combo.currentIndex())
        base = strip_audio_extension(self.output_edit.text().strip())
        if not base:
            base = strip_audio_extension(get_output_filename(self.input_file))
        self.output_edit.setText(base)

        if output_format in ("m4b", "both") and not ffmpeg_available():
            QMessageBox.critical(self, "缺少 ffmpeg", INSTALL_HINT)
            return

        if epub_input:
            targets = {
                "mp3": [base + ".mp3"],
                "m4b": [base + ".m4b"],
                "both": [base + ".mp3", base + ".m4b"],
            }[output_format]
        else:
            mp3_path, m4b_path = output_paths(self.input_file, base, output_format)
            targets = {
                "mp3": [mp3_path],
                "m4b": [m4b_path],
                "both": [mp3_path, m4b_path],
            }[output_format]
        existing = [path for path in targets if os.path.exists(path)]
        if existing:
            answer = QMessageBox.question(
                self,
                "文件已存在",
                "以下文件已存在：\n" + "\n".join(existing) + "\n\n是否覆盖？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        voice = self.voice_combo.itemData(self.voice_combo.currentIndex())
        self.progress_bar.setValue(0)
        self.progress_label.setText("0%")
        self._set_busy(True)

        self.convert_thread = ConvertThread(
            self.input_file, base, voice, output_format, self.workers_spin.value()
        )
        self.convert_thread.progress_updated.connect(self.on_progress_updated)
        self.convert_thread.conversion_finished.connect(self.on_convert_finished)
        self.convert_thread.conversion_cancelled.connect(self.on_convert_cancelled)
        self.convert_thread.conversion_error.connect(self.on_convert_error)
        self.convert_thread.start()

    def on_cancel_button_click(self) -> None:
        if self.convert_thread and self.convert_thread.isRunning():
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("正在取消…")
            self.convert_thread.cancel()

    def _set_busy(self, busy: bool) -> None:
        self.convert_button.setEnabled(not busy)
        self.preview_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.output_edit.setEnabled(not busy)
        self.output_button.setEnabled(not busy)
        self.voice_combo.setEnabled(not busy)
        self.format_combo.setEnabled(not busy)
        self.workers_spin.setEnabled(not busy)

    def on_progress_updated(self, value: int) -> None:
        self.progress_bar.setValue(value)
        self.progress_label.setText(f"{value}%")

    def on_convert_finished(self, result: ConversionResult) -> None:
        self.progress_bar.setValue(100)
        self.progress_label.setText("转化完成！")
        self._set_busy(False)
        lines = ["转化完成！"]
        if result.chapter_count:
            lines.append(f"章节数: {result.chapter_count}")
        if result.chapters_dir:
            lines.append(f"章节文件: {result.chapters_dir}")
        if result.mp3:
            lines.append(f"MP3: {result.mp3}")
        if result.m4b:
            lines.append(f"M4B: {result.m4b}")
        QMessageBox.information(self, "成功", "\n".join(lines))

    def on_convert_cancelled(self) -> None:
        self._set_busy(False)
        self.progress_label.setText("已取消")
        QMessageBox.information(
            self, "已取消", "已取消转换。已完成的音频块会保留，再次点击转化可继续。"
        )

    def on_convert_error(self, error_msg: str) -> None:
        self._set_busy(False)
        self.progress_label.setText("转化失败")
        QMessageBox.critical(self, "错误", f"转化失败: {error_msg}")

    # ---------- 试听 ----------

    def on_preview_button_click(self) -> None:
        voice = self.voice_combo.itemData(self.voice_combo.currentIndex())
        preview_text = (
            "这是一段试听文本，用于测试语音效果。"
            if voice.startswith("zh")
            else "This is a sample text for testing the voice quality."
        )

        self.preview_button.setEnabled(False)
        self.preview_thread = PreviewThread(preview_text, voice)
        self.preview_thread.preview_finished.connect(
            lambda: self.preview_button.setEnabled(True)
        )
        self.preview_thread.preview_error.connect(self.on_preview_error)
        self.preview_thread.start()

    def on_preview_error(self, message: str) -> None:
        self.preview_button.setEnabled(True)
        QMessageBox.warning(self, "警告", f"试听失败: {message}")

    # ---------- 关闭 ----------

    def closeEvent(self, event) -> None:
        if self.convert_thread and self.convert_thread.isRunning():
            self.convert_thread.cancel()
            if not self.convert_thread.wait(5000):
                # 网络请求可能还在等待超时，强行退出会让 QThread 崩溃
                QMessageBox.information(
                    self, "正在取消", "正在取消转换，请稍候再关闭窗口。已完成的块会保留。"
                )
                event.ignore()
                return
        if self.preview_thread and self.preview_thread.isRunning():
            if not self.preview_thread.wait(5000):
                QMessageBox.information(
                    self, "试听中", "试听仍在进行，请稍候再关闭窗口。"
                )
                event.ignore()
                return
        event.accept()


def run_gui() -> None:
    app = QApplication(sys.argv)
    window = TextToAudioGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_gui()
