import os

def read_file(file_path):
    """读取文本文件内容"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return text
    except Exception as e:
        raise Exception(f"读取文件时出错: {e}")

def get_output_filename(input_file):
    """根据输入文件名生成输出文件名"""
    return os.path.splitext(input_file)[0] + ".mp3"

def check_file_exists(file_path):
    """检查文件是否存在"""
    return os.path.exists(file_path)
