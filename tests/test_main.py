import os
import tempfile
from ebook_to_audio.main import main
import sys

# 测试文本内容
TEST_TEXT = "这是一个测试文本，用于测试文本转音频功能。"

def test_main_with_short_text():
    """测试短文本转换"""
    # 创建临时测试文件
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode='w', encoding='utf-8') as f:
        f.write(TEST_TEXT)
        test_file = f.name
    
    try:
        # 运行主函数
        sys.argv = [sys.argv[0], test_file]
        main()
        
        # 检查输出文件是否生成
        output_file = os.path.splitext(test_file)[0] + ".mp3"
        assert os.path.exists(output_file), f"输出文件 {output_file} 未生成"
        assert os.path.getsize(output_file) > 0, f"输出文件 {output_file} 为空"
        print("短文本测试通过！")
        
    finally:
        # 清理临时文件
        if os.path.exists(test_file):
            os.unlink(test_file)
        output_file = os.path.splitext(test_file)[0] + ".mp3"
        if os.path.exists(output_file):
            os.unlink(output_file)

if __name__ == "__main__":
    test_main_with_short_text()
