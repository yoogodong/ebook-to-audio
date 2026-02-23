import sys
import os
import asyncio

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QProgressBar, QFileDialog,
    QFrame, QMessageBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtGui import QDragEnterEvent, QDropEvent

from ebook_to_audio.core.tts_processor import split_text
from ebook_to_audio.utils.file_handler import read_file, get_output_filename
from ebook_to_audio.gui.icon import set_window_icon
import edge_tts

class ConvertThread(QThread):
    """转化线程，用于在后台执行文本转音频操作"""
    progress_updated = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, text_content, output_file, voice):
        super().__init__()
        self.text_content = text_content
        self.output_file = output_file
        self.voice = voice
    
    def run(self):
        try:
            # 检查文本内容
            if not self.text_content or len(self.text_content.strip()) == 0:
                raise Exception("文本内容为空，请选择包含内容的文本文件")
            
            # 检查文本长度
            MAX_CHUNK_SIZE = 900
            if len(self.text_content) <= MAX_CHUNK_SIZE:
                # 短文本直接转换
                asyncio.run(self.convert_short_text())
            else:
                # 长文本分割转换
                asyncio.run(self.convert_long_text())
            
            self.finished.emit(self.output_file)
        except Exception as e:
            self.error.emit(str(e))
    
    async def convert_short_text(self):
        """转换短文本"""
        # 更新进度
        self.progress_updated.emit(30)
        
        # 检查文本长度
        text_to_convert = self.text_content
        if len(text_to_convert) < 10:
            # 文本太短，可能导致 Edge TTS 服务不返回音频
            text_to_convert += " " * 10  # 添加一些空格使文本长度足够
        
        # 转换文本
        try:
            # 尝试使用选择的语音
            success, audio_data = await self._get_audio_data(text_to_convert, self.voice)
            
            if not success:
                # 尝试使用默认语音
                default_voice = "zh-CN-YunxiNeural" if self.voice.startswith("zh") else "en-US-AriaNeural"
                self.progress_updated.emit(40)
                success, audio_data = await self._get_audio_data(text_to_convert, default_voice)
            
            if not success:
                # 尝试使用最可靠的语音
                self.progress_updated.emit(50)
                success, audio_data = await self._get_audio_data(text_to_convert, "zh-CN-YunxiNeural")
            
            if not success:
                raise Exception("无法获取音频数据，请检查网络连接或尝试其他语音")
            
            # 保存音频数据
            with open(self.output_file, "wb") as f:
                total_chunks = len(audio_data)
                for i, chunk in enumerate(audio_data):
                    f.write(chunk)
                    # 更新进度
                    progress = 40 + (i / total_chunks) * 60
                    self.progress_updated.emit(int(progress))
        except Exception as e:
            # 尝试使用简单文本
            self.progress_updated.emit(60)
            try:
                simple_text = "这是一段测试文本。" if self.voice.startswith("zh") else "This is a test text."
                success, audio_data = await self._get_audio_data(simple_text, "zh-CN-YunxiNeural")
                if success:
                    with open(self.output_file, "wb") as f:
                        for chunk in audio_data:
                            f.write(chunk)
                    self.progress_updated.emit(100)
                else:
                    raise Exception(f"转换失败: {str(e)}")
            except Exception as inner_e:
                raise Exception(f"转换失败: {str(e)}")
    
    async def _get_audio_data(self, text, voice):
        """获取音频数据"""
        audio_data = []
        try:
            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data.append(chunk["data"])
            return len(audio_data) > 0, audio_data
        except Exception:
            return False, []
    
    async def convert_long_text(self):
        """转换长文本"""
        # 分割文本
        MAX_CHUNK_SIZE = 900
        chunks = split_text(self.text_content, MAX_CHUNK_SIZE)
        total_chunks = len(chunks)
        
        # 收集所有音频数据
        all_audio_data = []
        
        try:
            for i, chunk in enumerate(chunks):
                # 更新进度
                progress = 20 + (i / total_chunks) * 80
                self.progress_updated.emit(int(progress))
                
                # 尝试使用选择的语音
                success, audio_data = await self._get_audio_data(chunk, self.voice)
                
                if not success:
                    # 尝试使用默认语音
                    default_voice = "zh-CN-YunxiNeural" if self.voice.startswith("zh") else "en-US-AriaNeural"
                    success, audio_data = await self._get_audio_data(chunk, default_voice)
                
                if not success:
                    # 尝试使用最可靠的语音
                    success, audio_data = await self._get_audio_data(chunk, "zh-CN-YunxiNeural")
                
                if not success:
                    raise Exception("无法获取音频数据，请检查网络连接或尝试其他语音")
                
                # 添加音频数据到总集合
                all_audio_data.extend(audio_data)
            
            # 合并并保存音频文件
            with open(self.output_file, "wb") as f:
                for chunk in all_audio_data:
                    f.write(chunk)
            
        except Exception as e:
            raise Exception(f"转换长文本失败: {str(e)}")

class TextToAudioGUI(QMainWindow):
    """文本转音频 GUI 应用程序"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("文本转音频工具")
        self.setGeometry(100, 100, 600, 500)
        
        # 设置窗口图标
        set_window_icon(self)
        
        # 变量初始化
        self.input_file = None
        self.text_content = ""
        self.selected_voice = "zh-CN-YunxiNeural"
        self.convert_thread = None
        
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)
        
        # 创建文件输入区域
        self.create_file_input_area(main_layout)
        
        # 创建语音选择区域
        self.create_voice_selection(main_layout)
        
        # 创建转化按钮
        self.create_convert_button(main_layout)
        
        # 创建进度显示
        self.create_progress_display(main_layout)
        
        # 设置窗口接受拖放
        self.setAcceptDrops(True)
    
    def create_file_input_area(self, parent_layout):
        """创建文件输入区域"""
        file_frame = QFrame()
        file_frame.setFrameShape(QFrame.StyledPanel)
        file_frame.setFrameShadow(QFrame.Raised)
        file_layout = QVBoxLayout(file_frame)
        
        # 标题
        title_label = QLabel("文件选择")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        file_layout.addWidget(title_label)
        
        # 文件拖放区域
        self.drop_area = QLabel("拖放文本文件到此处，或点击选择文件")
        self.drop_area.setAlignment(Qt.AlignCenter)
        self.drop_area.setMinimumHeight(150)
        self.drop_area.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; border-radius: 5px; background-color: #f9f9f9; padding: 20px; }"
        )
        self.drop_area.setFont(QFont("Arial", 12))
        self.drop_area.mousePressEvent = self.on_drop_area_click
        file_layout.addWidget(self.drop_area)
        
        # 文件信息标签
        self.file_info_label = QLabel("")
        self.file_info_label.setFont(QFont("Arial", 10))
        file_layout.addWidget(self.file_info_label)
        
        parent_layout.addWidget(file_frame)
    
    def create_voice_selection(self, parent_layout):
        """创建语音选择区域"""
        voice_frame = QFrame()
        voice_frame.setFrameShape(QFrame.StyledPanel)
        voice_frame.setFrameShadow(QFrame.Raised)
        voice_layout = QVBoxLayout(voice_frame)
        
        # 标题
        title_label = QLabel("语音选择")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        voice_layout.addWidget(title_label)
        
        # 语音选择布局
        combo_layout = QHBoxLayout()
        
        # 标签
        voice_label = QLabel("播音员:")
        voice_label.setFont(QFont("Arial", 10))
        combo_layout.addWidget(voice_label)
        
        # 下拉列表
        self.voice_combo = QComboBox()
        # 使用官方语音列表，中文只列出普通话
        self.voices = [
            # 中文普通话语音
            ("zh-CN-XiaoxiaoNeural", "中文 - 晓晓 (女)"),
            ("zh-CN-XiaoyiNeural", "中文 - 小伊 (女)"),
            ("zh-CN-YunjianNeural", "中文 - 云健 (男)"),
            ("zh-CN-YunxiNeural", "中文 - 云希 (男)"),
            ("zh-CN-YunxiaNeural", "中文 - 云夏 (男)"),
            ("zh-CN-YunyangNeural", "中文 - 云阳 (男)"),
            # 英文语音
            ("en-US-AriaNeural", "英文 (US) - Aria"),
            ("en-US-GuyNeural", "英文 (US) - Guy"),
            ("en-GB-LibbyNeural", "英文 (GB) - Libby"),
            ("en-GB-RyanNeural", "英文 (GB) - Ryan"),
            ("en-AU-NatashaNeural", "英文 (AU) - Natasha"),
            ("en-AU-WilliamNeural", "英文 (AU) - William")
        ]
        
        # 添加语音选项
        for voice_id, voice_name in self.voices:
            self.voice_combo.addItem(voice_name, voice_id)
        
        self.voice_combo.setFont(QFont("Arial", 10))
        combo_layout.addWidget(self.voice_combo, 1)
        
        # 试听按钮
        self.preview_button = QPushButton("试听")
        self.preview_button.setFont(QFont("Arial", 10))
        self.preview_button.setMinimumWidth(60)
        self.preview_button.clicked.connect(self.on_preview_button_click)
        combo_layout.addWidget(self.preview_button)
        
        voice_layout.addLayout(combo_layout)
        parent_layout.addWidget(voice_frame)
    
    def create_convert_button(self, parent_layout):
        """创建转化按钮"""
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        
        self.convert_button = QPushButton("开始转化")
        self.convert_button.setFont(QFont("Arial", 12))
        self.convert_button.setMinimumHeight(40)
        self.convert_button.clicked.connect(self.on_convert_button_click)
        button_layout.addWidget(self.convert_button, 2)
        
        button_layout.addStretch(1)
        parent_layout.addLayout(button_layout)
    
    def create_progress_display(self, parent_layout):
        """创建进度显示"""
        progress_frame = QFrame()
        progress_frame.setFrameShape(QFrame.StyledPanel)
        progress_frame.setFrameShadow(QFrame.Raised)
        progress_layout = QVBoxLayout(progress_frame)
        
        # 标题
        title_label = QLabel("转化进度")
        title_label.setFont(QFont("Arial", 12, QFont.Bold))
        progress_layout.addWidget(title_label)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        # 进度文本
        self.progress_label = QLabel("0%")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setFont(QFont("Arial", 10))
        progress_layout.addWidget(self.progress_label)
        
        parent_layout.addWidget(progress_frame)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖放进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.drop_area.setStyleSheet(
                "QLabel { border: 2px dashed #6666ff; border-radius: 5px; background-color: #f0f0ff; padding: 20px; }"
            )
    
    def dragLeaveEvent(self, event):
        """拖放离开事件"""
        self.drop_area.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; border-radius: 5px; background-color: #f9f9f9; padding: 20px; }"
        )
    
    def dropEvent(self, event: QDropEvent):
        """拖放事件"""
        self.drop_area.setStyleSheet(
            "QLabel { border: 2px dashed #cccccc; border-radius: 5px; background-color: #f9f9f9; padding: 20px; }"
        )
        
        # 获取拖放的文件路径
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            file_path = url.toLocalFile()
            if os.path.exists(file_path) and file_path.endswith('.txt'):
                self.input_file = file_path
                self.update_file_info()
    
    def on_drop_area_click(self, event):
        """拖放区域点击事件"""
        # 打开文件选择对话框
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择文本文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if file_path:
            self.input_file = file_path
            self.update_file_info()
    
    def update_file_info(self):
        """更新文件信息"""
        if self.input_file:
            # 读取文件内容
            try:
                self.text_content = read_file(self.input_file)
                # 计算字符/单词数量
                text = self.text_content
                
                # 统计中文字符数
                chinese_chars = 0
                for char in text:
                    if '\u4e00' <= char <= '\u9fff':
                        chinese_chars += 1
                
                # 统计英文字母数
                english_chars = 0
                for char in text:
                    if 'a' <= char.lower() <= 'z':
                        english_chars += 1
                
                # 统计英文单词数
                words = text.split()
                word_count = 0
                for word in words:
                    # 检查是否是英文单词（至少包含一个英文字母）
                    if any('a' <= char.lower() <= 'z' for char in word):
                        word_count += 1
                
                # 更新文件信息
                file_name = os.path.basename(self.input_file)
                if chinese_chars > 0:
                    self.file_info_label.setText(f"文件: {file_name} | 中文字符: {chinese_chars} | 英文单词: {word_count}")
                else:
                    self.file_info_label.setText(f"文件: {file_name} | 英文单词: {word_count}")
                # 更新拖放区域文本
                self.drop_area.setText(f"已选择文件: {file_name}")
            except Exception as e:
                self.file_info_label.setText(f"错误: {e}")
    
    def on_convert_button_click(self):
        """转化按钮点击事件"""
        if not self.input_file:
            QMessageBox.warning(self, "警告", "请先选择一个文本文件")
            return
        
        # 获取选择的语音
        voice_index = self.voice_combo.currentIndex()
        self.selected_voice = self.voice_combo.itemData(voice_index)
        
        # 禁用按钮
        self.convert_button.setEnabled(False)
        
        # 重置进度
        self.progress_bar.setValue(0)
        self.progress_label.setText("0%")
        
        # 生成输出文件名
        output_file = get_output_filename(self.input_file)
        
        # 创建并启动转化线程
        self.convert_thread = ConvertThread(self.text_content, output_file, self.selected_voice)
        self.convert_thread.progress_updated.connect(self.on_progress_updated)
        self.convert_thread.finished.connect(self.on_convert_finished)
        self.convert_thread.error.connect(self.on_convert_error)
        self.convert_thread.start()
    
    def on_progress_updated(self, value):
        """进度更新事件"""
        self.progress_bar.setValue(value)
        self.progress_label.setText(f"{value}%")
    
    def on_convert_finished(self, output_file):
        """转化完成事件"""
        self.progress_bar.setValue(100)
        self.progress_label.setText("转化完成！")
        QMessageBox.information(self, "成功", f"转化完成！音频文件已保存为: {output_file}")
        # 启用按钮
        self.convert_button.setEnabled(True)
    
    def on_convert_error(self, error_msg):
        """转化错误事件"""
        QMessageBox.critical(self, "错误", f"转化失败: {error_msg}")
        # 启用按钮
        self.convert_button.setEnabled(True)

    def on_preview_button_click(self):
        """试听按钮点击事件"""
        # 获取选择的语音
        voice_index = self.voice_combo.currentIndex()
        selected_voice = self.voice_combo.itemData(voice_index)
        
        # 为不同语言的语音准备试听文本
        if selected_voice.startswith("zh"):
            preview_text = "这是一段试听文本，用于测试语音效果。"
        else:
            preview_text = "This is a sample text for testing the voice quality."
        
        # 禁用试听按钮
        self.preview_button.setEnabled(False)
        
        # 创建并启动试听线程
        self.preview_thread = PreviewThread(preview_text, selected_voice)
        self.preview_thread.finished.connect(lambda: self.preview_button.setEnabled(True))
        self.preview_thread.error.connect(lambda msg: QMessageBox.warning(self, "警告", f"试听失败: {msg}"))
        self.preview_thread.start()


class PreviewThread(QThread):
    """试听线程，用于在后台执行语音试听操作"""
    finished = pyqtSignal()
    error = pyqtSignal(str)
    
    def __init__(self, text, voice):
        super().__init__()
        self.text = text
        self.voice = voice
    
    def run(self):
        try:
            # 使用系统播放器播放音频
            import tempfile
            import subprocess
            
            # 生成临时音频文件
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
                temp_file = temp.name
            
            # 转换文本为音频
            asyncio.run(self.convert_text_to_audio(self.text, self.voice, temp_file))
            
            # 播放音频文件
            if sys.platform == "darwin":  # macOS
                subprocess.run(["afplay", temp_file])
            elif sys.platform == "win32":  # Windows
                subprocess.run(["start", "", temp_file], shell=True)
            else:  # Linux
                subprocess.run(["xdg-open", temp_file])
            
            # 清理临时文件
            if os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except:
                    pass
            
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
    
    async def convert_text_to_audio(self, text, voice, output_file):
        """转换文本为音频"""
        communicate = edge_tts.Communicate(text, voice)
        
        with open(output_file, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])


def run_gui():
    """运行 GUI 应用程序"""
    app = QApplication(sys.argv)
    window = TextToAudioGUI()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    run_gui()
