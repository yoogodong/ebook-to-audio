import asyncio
import sys
from ebook_to_audio.core.tts_processor import convert_text_to_audio, convert_long_text_to_audio, MAX_CHUNK_SIZE
from ebook_to_audio.utils.file_handler import read_file, get_output_filename, check_file_exists

def main():
    print("欢迎使用文本转音频工具！")
    
    # 检查命令行参数
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        # 获取用户输入的文本文件名
        input_file = input("请输入文本文件名: ")
    
    # 检查文件是否存在
    if not check_file_exists(input_file):
        print(f"错误: 文件 '{input_file}' 不存在！")
        return
    
    # 读取文本文件内容
    try:
        text = read_file(input_file)
    except Exception as e:
        print(f"错误: {e}")
        return
    
    # 生成输出文件名
    output_file = get_output_filename(input_file)
    
    print(f"正在将文本转换为音频...")
    print(f"输入文件: {input_file}")
    print(f"输出文件: {output_file}")
    print(f"文本长度: {len(text)} 字符")
    
    # 检查文本长度
    if len(text) <= MAX_CHUNK_SIZE:
        print("文本长度在限制范围内，直接转换...")
        # 异步转换文本为音频
        asyncio.run(convert_text_to_audio(text, output_file))
    else:
        print(f"文本长度超过限制，需要分割成多个部分转换...")
        # 分割文本并转换
        asyncio.run(convert_long_text_to_audio(text, output_file))
    
    print(f"转换完成！音频文件已保存为: {output_file}")

if __name__ == "__main__":
    main()
