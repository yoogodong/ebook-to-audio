#!/usr/bin/env python3
"""测试异步转换功能"""

import sys
import os
import asyncio

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

async def test_async_conversion():
    """测试异步转换"""
    try:
        print("正在测试异步转换功能...")
        
        # 测试edge-tts基本功能
        import edge_tts
        
        # 测试简单文本转换
        text = "这是一个测试句子。"
        voice = "zh-CN-YunxiNeural"
        
        print(f"正在测试转换文本: {text}")
        print(f"使用语音: {voice}")
        
        communicate = edge_tts.Communicate(text, voice)
        
        # 生成音频并保存到文件
        output_file = "test_output.mp3"
        audio_received = False
        
        with open(output_file, "wb") as f:
            total_chunks = 0
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                    total_chunks += 1
                    audio_received = True
                elif chunk["type"] == "WordBoundary":
                    # 忽略单词边界事件
                    pass
        
        if audio_received:
            print(f"异步转换测试通过，生成了 {total_chunks} 个音频块")
            print(f"输出文件: {output_file}")
            
            # 检查文件大小
            if os.path.exists(output_file):
                file_size = os.path.getsize(output_file)
                print(f"输出文件大小: {file_size} 字节")
        else:
            print("错误: 没有收到音频数据")
        
        # 清理测试文件
        if os.path.exists(output_file):
            os.remove(output_file)
        
        print("异步转换功能测试完成")
        
    except Exception as e:
        print(f"错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_async_conversion())
