#!/usr/bin/env python3
"""最小化的PyQt5测试脚本"""

import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel

class MinimalGUI(QMainWindow):
    """最小化的GUI应用程序"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("测试GUI")
        self.setGeometry(100, 100, 400, 300)
        
        # 添加一个标签
        label = QLabel("GUI测试成功！", self)
        label.setGeometry(100, 100, 200, 50)

def run_test_gui():
    """运行测试GUI"""
    print("正在启动GUI...")
    app = QApplication(sys.argv)
    window = MinimalGUI()
    window.show()
    print("GUI已显示，等待关闭...")
    sys.exit(app.exec_())

if __name__ == "__main__":
    run_test_gui()
