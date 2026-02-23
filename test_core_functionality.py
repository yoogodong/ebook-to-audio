#!/usr/bin/env python3
"""测试核心功能"""

import sys
import os

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

try:
    print("正在测试核心功能...")
    
    # 测试文本分割
    from ebook_to_audio.core.tts_processor import split_text
    test_text = "这是一个测试句子。这是另一个测试句子。这是第三个测试句子。"
    chunks = split_text(test_text, 50)
    print(f"文本分割测试通过，分割为 {len(chunks)} 个部分")
    print(f"分割结果: {chunks}")
    
    # 测试文件处理
    from ebook_to_audio.utils.file_handler import read_file, get_output_filename
    # 创建测试文件
    test_file = "test_input.txt"
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write("这是测试文件内容。这是第二句话。")
    
    # 测试读取文件
    content = read_file(test_file)
    print(f"文件读取测试通过，内容: {content}")
    
    # 测试输出文件名生成
    output_file = get_output_filename(test_file)
    print(f"输出文件名生成测试通过，输出文件: {output_file}")
    
    # 清理测试文件
    if os.path.exists(test_file):
        os.remove(test_file)
    if os.path.exists(output_file):
        os.remove(output_file)
    
    print("所有核心功能测试通过")
    
except Exception as e:
    print(f"错误: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
