"""兼容入口：``python main.py [文件]``，实际调用安装后的包。

推荐直接使用 ``ebook-to-audio`` 命令或 ``scripts/run.sh``。
"""

from ebook_to_audio.main import main

if __name__ == "__main__":
    raise SystemExit(main())
