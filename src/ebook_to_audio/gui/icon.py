#!/usr/bin/env python3
"""图标处理模块"""

import os
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt5.QtCore import QSize, Qt


def get_app_icon():
    """获取应用图标"""
    # 创建一个32x32的图标
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(255, 255, 255))  # 白色背景
    
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    
    # 绘制书本
    # 书本主体
    painter.setBrush(QColor(240, 240, 240))  # 浅灰色
    painter.setPen(QColor(100, 100, 100))  # 深灰色边框
    painter.drawRect(6, 8, 20, 16)  # 书本主体
    
    # 书脊
    painter.setBrush(QColor(200, 200, 200))  # 中灰色
    painter.drawRect(6, 8, 3, 16)  # 书脊
    
    # 书页
    painter.setBrush(QColor(255, 255, 255))  # 白色
    painter.drawRect(8, 10, 16, 2)  # 第一行
    painter.drawRect(8, 14, 16, 2)  # 第二行
    painter.drawRect(8, 18, 16, 2)  # 第三行
    
    # 绘制声音波形
    # 波形线
    painter.setPen(QColor(50, 150, 250))  # 蓝色
    painter.setBrush(Qt.NoBrush)
    
    # 绘制声波
    wave_points = [
        (22, 12), (24, 8), (26, 12), (28, 4), (30, 12)
    ]
    
    for i in range(len(wave_points) - 1):
        painter.drawLine(wave_points[i][0], wave_points[i][1], 
                        wave_points[i+1][0], wave_points[i+1][1])
    
    # 绘制声波底部
    painter.drawLine(22, 12, 30, 12)
    
    painter.end()
    
    # 创建图标
    icon = QIcon(pixmap)
    return icon


def set_window_icon(window):
    """设置窗口图标"""
    icon = get_app_icon()
    window.setWindowIcon(icon)
