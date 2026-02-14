import edge_tts
import asyncio
import os
import sys
import tempfile

# 微软 Edge TTS 服务的字符数限制（根据文档，一般为 1000 字符左右）
MAX_CHUNK_SIZE = 900

def main():
    print("欢迎使用文本转音频工具！")
    
    # 检查命令行参数
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    else:
        # 获取用户输入的文本文件名
        input_file = input("请输入文本文件名: ")
    
    # 检查文件是否存在
    if not os.path.exists(input_file):
        print(f"错误: 文件 '{input_file}' 不存在！")
        return
    
    # 读取文本文件内容
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return
    
    # 生成输出文件名
    output_file = os.path.splitext(input_file)[0] + ".mp3"
    
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

async def convert_text_to_audio(text, output_file):
    """转换单个文本片段为音频"""
    # 创建 edge-tts 实例
    communicate = edge_tts.Communicate(text, "zh-CN-YunxiNeural")
    
    # 生成音频并保存到文件
    with open(output_file, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])

async def convert_long_text_to_audio(long_text, output_file):
    """分割长文本并转换为音频，然后合并"""
    # 分割文本
    chunks = split_text(long_text, MAX_CHUNK_SIZE)
    print(f"文本已分割为 {len(chunks)} 个部分")
    
    # 创建临时文件来存储每个音频片段
    temp_files = []
    
    try:
        # 转换每个文本片段
        for i, chunk in enumerate(chunks):
            print(f"正在转换第 {i+1}/{len(chunks)} 部分...")
            # 创建临时文件
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
                temp_file = temp.name
                temp_files.append(temp_file)
            
            # 转换当前片段
            await convert_text_to_audio(chunk, temp_file)
        
        # 合并音频片段
        print("正在合并音频片段...")
        merge_audio_files(temp_files, output_file)
        print("音频片段合并完成！")
        
    finally:
        # 清理临时文件
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except:
                    pass

def split_text(text, max_chunk_size):
    """分割文本为多个片段，每个片段不超过 max_chunk_size 字符"""
    chunks = []
    current_chunk = ""
    
    # 按句子分割，尽量保持句子完整
    sentences = text.split('。')
    
    for sentence in sentences:
        sentence += '。'
        
        if len(current_chunk) + len(sentence) <= max_chunk_size:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = sentence
            else:
                # 单个句子超过限制，强制分割
                for i in range(0, len(sentence), max_chunk_size):
                    chunks.append(sentence[i:i+max_chunk_size])
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks

def merge_audio_files(input_files, output_file):
    """合并多个 MP3 文件为一个"""
    # 简单的二进制合并
    with open(output_file, "wb") as out_f:
        for in_file in input_files:
            with open(in_file, "rb") as f:
                out_f.write(f.read())

if __name__ == "__main__":
    main()
