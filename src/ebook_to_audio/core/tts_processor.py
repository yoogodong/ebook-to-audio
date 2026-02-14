import edge_tts
import asyncio
import os
import tempfile

# 微软 Edge TTS 服务的字符数限制（根据文档，一般为 1000 字符左右）
MAX_CHUNK_SIZE = 900

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
    """分割文本为多个片段，每个片段不超过 max_chunk_size 字符，保持句子完整性"""
    chunks = []
    current_chunk = ""
    
    # 句子结束符集合
    sentence_endings = ['.', '。', '!', '！', '?', '？', ';', '；', ':', '：']
    
    # 分割句子的辅助函数
    def split_into_sentences(text):
        sentences = []
        current_sentence = ""
        
        for char in text:
            current_sentence += char
            if char in sentence_endings:
                sentences.append(current_sentence)
                current_sentence = ""
        
        # 处理最后一个没有结束符的句子
        if current_sentence.strip():
            sentences.append(current_sentence)
        
        return sentences
    
    # 按句子分割
    sentences = split_into_sentences(text)
    
    for sentence in sentences:
        if len(current_chunk) + len(sentence) <= max_chunk_size:
            current_chunk += sentence
        else:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = sentence
            else:
                # 单个句子超过限制，强制分割
                # 尝试在最近的句子结束符处分割
                split_point = max_chunk_size
                # 向前查找最近的句子结束符
                for i in range(max_chunk_size, max(0, max_chunk_size - 100), -1):
                    if i < len(sentence) and sentence[i] in sentence_endings:
                        split_point = i + 1
                        break
                
                # 分割句子
                chunks.append(sentence[:split_point])
                # 递归处理剩余部分
                chunks.extend(split_text(sentence[split_point:], max_chunk_size))
    
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
