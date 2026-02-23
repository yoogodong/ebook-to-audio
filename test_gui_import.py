#!/usr/bin/env python3
"""测试GUI模块导入和初始化"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    print("正在导入GUI模块...")
    from ebook_to_audio.gui.app import TextToAudioGUI
    print("GUI模块导入成功")
    
    print("正在检查依赖...")
    import edge_tts
    print("edge_tts库导入成功")
    
    import PyQt5
    print("PyQt5库导入成功")
    
    print("所有依赖检查通过")
    
except Exception as e:
    print(f"错误: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
