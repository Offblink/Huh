import sys
import os
import time
import threading
import requests
import base64
import json
from PIL import Image
from io import BytesIO
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass

# PyQt6 imports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QLabel, QTextEdit, QComboBox,
    QMessageBox, QSystemTrayIcon, QMenu, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, pyqtSlot, QPoint
from PyQt6.QtGui import QIcon, QFont, QKeySequence, QShortcut, QColor, QPixmap

# 截图相关
import pyautogui
import pygetwindow as gw

# 全局热键
try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False
    print("警告: 未安装keyboard库，将使用PyQt热键（仅在应用激活时有效）")

# OCR和翻译的API配置
OCR_API_KEY = "pArLAkbEndovHYLzO6AfhAFS"
OCR_SECRET_KEY = "PUxGVyeCgXtY8FvmAR6MtwlrXCBImNmR"
OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"

TRANSLATION_KEY = "e7509fc557394a619bc89d9bc44172ce.qY4uSyCofHoCfQSX"
TRANSLATION_URL = "https://api.translate.yandex.net/api/v1.5/tr.json/translate"
TRANSLATION_LANG = "en-zh"

# 数据类定义
@dataclass
class WindowInfo:
    """窗口信息"""
    index: int
    title: str
    handle: any
    position: Tuple[int, int]
    size: Tuple[int, int]


class BaiduOCR:
    """百度OCR服务封装"""
    
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.access_token = None
        self.token_expire_time = 0
        
    def get_access_token(self) -> str:
        """获取访问令牌"""
        if self.access_token and time.time() < self.token_expire_time:
            return self.access_token
            
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key
        }
        
        try:
            response = requests.post(url, params=params)
            if response.status_code == 200:
                data = response.json()
                self.access_token = data.get("access_token")
                self.token_expire_time = time.time() + data.get("expires_in", 2592000) - 300
                return self.access_token
            else:
                print(f"获取token失败: {response.text}")
                return None
        except Exception as e:
            print(f"获取token异常: {e}")
            return None
    
    def recognize_text(self, image_path: str) -> Tuple[bool, str, List[Dict]]:
        """识别图片中的文字"""
        token = self.get_access_token()
        if not token:
            return False, "获取OCR令牌失败", []
        
        with open(image_path, 'rb') as f:
            img_data = f.read()
        
        img_base64 = base64.b64encode(img_data).decode('utf-8')
        
        url = f"{OCR_URL}?access_token={token}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'image': img_base64}
        
        try:
            response = requests.post(url, headers=headers, data=data)
            result = response.json()
            
            if 'error_code' in result:
                if result['error_code'] == 100:
                    return False, "参数错误，请检查图片格式", []
                else:
                    return False, f"OCR识别失败: {result.get('error_msg', '未知错误')}", []
            
            words_result = result.get('words_result', [])
            text_blocks = []
            all_text = ""
            
            for item in words_result:
                text = item.get('words', '')
                if text:
                    all_text += text + "\n"
                    text_blocks.append({
                        'text': text,
                        'location': item.get('location', {})
                    })
            
            return True, all_text.strip(), text_blocks
            
        except Exception as e:
            return False, f"OCR请求异常: {e}", []


class Translator:
    """翻译服务封装"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.api_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        self.model = "glm-4-flash"
        
    def translate(self, text: str) -> Tuple[bool, str]:
        """使用DeepSeek API翻译文本"""
        if not text or not text.strip():
            return True, ""
        
        messages = [
            {
                "role": "system", 
                "content": "你是一个专业翻译助手，专门将英文翻译成中文。请准确翻译，保持专业术语，确保译文流畅自然。"
            },
            {
                "role": "user", 
                "content": f"请将以下英文内容翻译成中文，只输出翻译结果，不要添加任何解释：\n\n{text}"
            }
        ]
        
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'model': self.model,
            'messages': messages,
            'max_tokens': 2000,
            'temperature': 0.3
        }
        
        try:
            response = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
            
            if response.status_code != 200:
                return False, f"翻译API请求失败: {response.status_code}"
            
            data = response.json()
            
            if 'choices' in data and len(data['choices']) > 0:
                translated_text = data['choices'][0]['message']['content']
                return True, translated_text.strip()
            else:
                return False, f"翻译API响应格式错误: {data}"
                
        except requests.exceptions.Timeout:
            return False, "翻译请求超时，请检查网络连接"
        except requests.exceptions.RequestException as e:
            return False, f"翻译请求异常: {e}"
        except Exception as e:
            return False, f"翻译处理异常: {e}"

class WindowCapturer:
    """窗口截图工具"""
    
    def __init__(self):
        self.available_windows = []
        self.update_window_list()
    
    def update_window_list(self) -> List[WindowInfo]:
        """更新当前所有窗口列表"""
        self.available_windows = []
        windows = []
        
        for i, win in enumerate(gw.getAllWindows()):
            if win.title and win.title.strip():
                win_info = WindowInfo(
                    index=i,
                    title=win.title,
                    handle=win,
                    position=(win.left, win.top),
                    size=(win.width, win.height)
                )
                self.available_windows.append(win_info)
                windows.append(win_info)
        
        return windows
    
    def get_window_by_index(self, index: int) -> Optional[WindowInfo]:
        """通过索引获取窗口信息"""
        if 0 <= index < len(self.available_windows):
            return self.available_windows[index]
        return None
    
    def get_window_by_title(self, title: str) -> Optional[WindowInfo]:
        """通过标题获取窗口信息"""
        for win_info in self.available_windows:
            if title in win_info.title:
                return win_info
        return None
    
    def capture_window(self, window_title: str, save_dir: str = "temp_screenshots") -> Tuple[Optional[Image.Image], Optional[str]]:
        """截图指定窗口"""
        try:
            windows = gw.getWindowsWithTitle(window_title)
            if not windows:
                print(f"未找到窗口: {window_title}")
                return None, None
            
            window = windows[0]
            
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)
            
            if window.isMinimized:
                window.restore()
            window.activate()
            time.sleep(0.3)
            
            screenshot = pyautogui.screenshot(region=(
                window.left,
                window.top,
                window.width,
                window.height
            ))
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_title = "".join(c for c in window_title if c.isalnum() or c in (' ', '-', '_'))[:30]
            filename = f"{safe_title}_{timestamp}.png"
            save_path = os.path.join(save_dir, filename)
            
            screenshot.save(save_path)
            print(f"截图保存: {save_path}")
            
            return screenshot, save_path
            
        except Exception as e:
            print(f"截图失败: {e}")
            return None, None


class WorkerThread(QThread):
    """工作线程，处理OCR和翻译"""
    
    finished = pyqtSignal(str, str, str)  # 状态, 原文, 译文
    progress = pyqtSignal(str)
    
    def __init__(self, image_path: str, ocr: BaiduOCR, translator: Translator):
        super().__init__()
        self.image_path = image_path
        self.ocr = ocr
        self.translator = translator
        
    def run(self):
        try:
            self.progress.emit("正在识别图片中的文字...")
            
            # OCR识别
            success, text, _ = self.ocr.recognize_text(self.image_path)
            
            if not success or not text:
                self.finished.emit("error", "", f"OCR识别失败: {text}")
                return
                
            # 简化：只提取和翻译英文内容
            english_lines = []
            for line in text.split('\n'):
                line = line.strip()
                if line:
                    # 检查行中是否包含英文字母
                    has_english = any(c.isascii() and c.isalpha() for c in line)
                    if has_english:
                        english_lines.append(line)
            
            if not english_lines:
                self.finished.emit("no_english", "", "未检测到英文文本，跳过翻译")
                return
                
            english_text = '\n'.join(english_lines)
            self.progress.emit("检测到英文文本，正在翻译...")
            
            # 翻译
            trans_success, translated = self.translator.translate(english_text)
            
            if trans_success:
                self.finished.emit("success", english_text, translated)
            else:
                self.finished.emit("partial", english_text, f"翻译失败: {translated}")
                
        except Exception as e:
            self.finished.emit("error", "", f"处理异常: {str(e)}")


class GlobalHotkeyManager:
    """全局热键管理器"""
    
    def __init__(self, hotkey: str, callback):
        self.hotkey = hotkey
        self.callback = callback
        self.listening = False
        
    def start(self):
        """开始监听热键"""
        if not HAS_KEYBOARD:
            print("警告: keyboard库未安装，全局热键不可用")
            return False
            
        try:
            keyboard.add_hotkey(self.hotkey, self.callback)
            self.listening = True
            print(f"全局热键 {self.hotkey} 已启用")
            return True
        except Exception as e:
            print(f"启用全局热键失败: {e}")
            return False
    
    def stop(self):
        """停止监听热键"""
        if HAS_KEYBOARD and self.listening:
            try:
                keyboard.remove_hotkey(self.hotkey)
                self.listening = False
                print("全局热键已禁用")
            except:
                pass


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.current_window = None
        self.ocr = BaiduOCR(OCR_API_KEY, OCR_SECRET_KEY)
        self.translator = Translator(TRANSLATION_KEY)
        self.capturer = WindowCapturer()
        self.hotkey_manager = None
        
        self.init_ui()
        self.init_system_tray()
        self.setup_hotkeys()
        
    def init_ui(self):
        self.setWindowTitle("Huh")
        self.setGeometry(100, 100, 800, 600)
        
        # 设置图标
        self.setWindowIcon(self.create_icon())
        
        # 中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        
        # 窗口选择区域
        window_section = QWidget()
        window_layout = QVBoxLayout()
        
        # 窗口列表标签
        window_label = QLabel("当前可用窗口（双击选择）:")
        window_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        
        # 窗口列表
        self.window_list = QListWidget()
        self.window_list.setMinimumHeight(200)
        # 添加双击信号连接
        self.window_list.itemDoubleClicked.connect(self.select_window_double_click)
        
        # 按钮区域
        button_widget = QWidget()
        button_layout = QHBoxLayout()
        
        self.refresh_btn = QPushButton("刷新窗口列表")
        self.refresh_btn.clicked.connect(self.refresh_windows)
        
        # 移除选择按钮，因为现在用双击
        self.test_btn = QPushButton("测试截图")
        self.test_btn.clicked.connect(self.test_capture)
        
        button_layout.addWidget(self.refresh_btn)
        button_layout.addWidget(self.test_btn)
        button_layout.addStretch()
        button_widget.setLayout(button_layout)
        
        # 当前选择显示
        self.current_window_label = QLabel("当前未选择窗口（双击列表中的窗口进行选择）")
        self.current_window_label.setStyleSheet("font-weight: bold; color: #d9534f;")
        
        window_layout.addWidget(window_label)
        window_layout.addWidget(self.window_list)
        window_layout.addWidget(button_widget)
        window_layout.addWidget(self.current_window_label)
        window_section.setLayout(window_layout)
        
        # 状态和日志区域
        status_section = QWidget()
        status_layout = QVBoxLayout()
        
        status_label = QLabel("状态信息:")
        status_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(150)
        self.status_text.setPlaceholderText("状态信息将显示在这里...")
        
        status_layout.addWidget(status_label)
        status_layout.addWidget(self.status_text)
        status_section.setLayout(status_layout)
        
        # 控制区域
        control_section = QWidget()
        control_layout = QHBoxLayout()
        
        self.hotkey_status = QLabel("全局热键: Ctrl+O (按下此组合键翻译当前窗口)")
        self.hotkey_status.setStyleSheet("color: #5cb85c; font-weight: bold;")
        
        self.minimize_btn = QPushButton("最小化到托盘")
        self.minimize_btn.clicked.connect(self.hide)
        
        self.exit_btn = QPushButton("退出")
        self.exit_btn.clicked.connect(self.close_application)
        
        control_layout.addWidget(self.hotkey_status)
        control_layout.addStretch()
        control_layout.addWidget(self.minimize_btn)
        control_layout.addWidget(self.exit_btn)
        control_section.setLayout(control_layout)
        
        # 添加到主布局
        main_layout.addWidget(window_section)
        main_layout.addWidget(status_section)
        main_layout.addWidget(control_section)
        
        central_widget.setLayout(main_layout)
        
        # 初始化窗口列表
        self.refresh_windows()
        
    def create_icon(self):
        """创建应用图标 - 优先加载当前目录下的icon.ico文件"""
        # 检查当前目录是否有icon.ico文件
        icon_path = "icon.ico"
        if os.path.exists(icon_path):
            try:
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    print(f"已加载图标文件: {icon_path}")
                    return QIcon(pixmap)
            except Exception as e:
                print(f"加载图标文件失败: {e}")
        
        # 如果没有icon.ico文件，使用默认图标
        print("使用默认图标")
        from PyQt6.QtGui import QPainter, QBrush, QPen
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 绘制问号图标
        painter.setBrush(QBrush(QColor("#2b579a")))
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.drawEllipse(10, 10, 44, 44)
        
        painter.setPen(QPen(Qt.GlobalColor.white, 3))
        painter.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "?")
        
        painter.end()
        return QIcon(pixmap)
        
    def init_system_tray(self):
        """初始化系统托盘"""
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(self)
            self.tray_icon.setIcon(self.create_icon())
            
            tray_menu = QMenu()
            show_action = tray_menu.addAction("显示主窗口")
            show_action.triggered.connect(self.show)
            
            translate_action = tray_menu.addAction("翻译当前窗口")
            translate_action.triggered.connect(self.process_translation)
            
            tray_menu.addSeparator()
            quit_action = tray_menu.addAction("退出")
            quit_action.triggered.connect(self.close_application)
            
            self.tray_icon.setContextMenu(tray_menu)
            self.tray_icon.activated.connect(self.tray_icon_activated)
            self.tray_icon.show()
            self.tray_icon.setToolTip("Huh")
            
    def tray_icon_activated(self, reason):
        """托盘图标激活"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.activateWindow()
            
    def setup_hotkeys(self):
        """设置热键"""
        # PyQt热键
        qt_hotkey = QShortcut(QKeySequence("Ctrl+O"), self)
        qt_hotkey.activated.connect(self.process_translation)
        
        # 全局热键
        if HAS_KEYBOARD:
            self.hotkey_manager = GlobalHotkeyManager("ctrl+o", self.process_translation)
            if not self.hotkey_manager.start():
                self.log_message("警告: 全局热键启用失败，仅应用激活时热键可用", "warning")
        else:
            self.log_message("提示: 安装keyboard库可启用后台全局热键", "info")
            
    def refresh_windows(self):
        """刷新窗口列表"""
        self.window_list.clear()
        windows = self.capturer.update_window_list()
        
        for win_info in windows[:50]:
            display_text = f"{win_info.title[:60]}... [{win_info.size[0]}x{win_info.size[1]}]"
            self.window_list.addItem(display_text)
            
        self.log_message(f"已找到 {len(windows)} 个窗口")
        
    def select_window_double_click(self, item):
        """双击选择窗口"""
        current_row = self.window_list.row(item)
        if current_row >= 0 and current_row < len(self.capturer.available_windows):
            self.current_window = self.capturer.available_windows[current_row]
            self.current_window_label.setText(f"当前选择: {self.current_window.title[:50]}...")
            self.current_window_label.setStyleSheet("font-weight: bold; color: #5cb85c;")
            self.log_message(f"已选择窗口: {self.current_window.title}")
            
    def select_window(self):
        """旧的选择窗口方法（保留兼容性）"""
        current_row = self.window_list.currentRow()
        if current_row >= 0 and current_row < len(self.capturer.available_windows):
            self.current_window = self.capturer.available_windows[current_row]
            self.current_window_label.setText(f"当前选择: {self.current_window.title[:50]}...")
            self.current_window_label.setStyleSheet("font-weight: bold; color: #5cb85c;")
            self.log_message(f"已选择窗口: {self.current_window.title}")
        else:
            QMessageBox.warning(self, "警告", "请先选择一个窗口")
            
    def test_capture(self):
        """测试截图"""
        if not self.current_window:
            QMessageBox.warning(self, "警告", "请先选择一个窗口")
            return
            
        screenshot, path = self.capturer.capture_window(
            self.current_window.title,
            "test_screenshots"
        )
        
        if screenshot and path:
            self.log_message(f"测试截图成功: {path}")
            QMessageBox.information(self, "成功", f"截图已保存到: {path}")
        else:
            self.log_message("测试截图失败", "error")
            
    def process_translation(self):
        """处理翻译流程"""
        if not self.current_window:
            self.log_message("请先选择一个窗口", "warning")
            if self.isHidden():
                self.tray_icon.showMessage(
                    "",
                    "请先打开应用选择一个窗口",
                    QSystemTrayIcon.MessageIcon.NoIcon,
                    3000
                )
            return
            
        self.log_message(f"开始处理窗口: {self.current_window.title}")
        
        # 截图
        screenshot, image_path = self.capturer.capture_window(
            self.current_window.title,
            "translation_screenshots"
        )
        
        if not screenshot or not image_path:
            self.log_message("截图失败", "error")
            return
            
        self.log_message(f"截图成功: {image_path}")
        
        # 创建工作线程处理OCR和翻译
        self.worker = WorkerThread(image_path, self.ocr, self.translator)
        self.worker.progress.connect(self.log_message)
        self.worker.finished.connect(self.on_translation_finished)
        self.worker.start()
        
    def on_translation_finished(self, status: str, original: str, translated: str):
        """翻译完成回调 - 使用托盘消息显示结果"""
        if status == "success":
            self.log_message("翻译成功！")
            # 简化：使用托盘消息显示翻译结果，不显示标题和图标
            if len(translated) > 200:
                display_text = translated[:200] + "..."
            else:
                display_text = translated
                
            self.tray_icon.showMessage(
                "",  # 空标题
                display_text,
                QSystemTrayIcon.MessageIcon.NoIcon,  # 不显示图标
                15000  # 显示15秒
            )
            
        elif status == "partial":
            self.log_message(f"OCR成功但翻译失败: {translated}", "warning")
            self.tray_icon.showMessage(
                "",
                f"翻译失败: {translated[:100]}",
                QSystemTrayIcon.MessageIcon.NoIcon,
                5000
            )
            
        elif status == "no_english":
            self.log_message("未检测到英文文本，跳过翻译", "info")
            # 不显示任何消息，因为用户要求中文内容不显示
            
        else:  # error
            self.log_message(f"处理失败: {translated}", "error")
            if self.isHidden():
                self.tray_icon.showMessage(
                    "",
                    translated[:100] + "..." if len(translated) > 100 else translated,
                    QSystemTrayIcon.MessageIcon.NoIcon,
                    3000
                )
          
    def log_message(self, message: str, level: str = "info"):
        """记录状态消息"""
        timestamp = time.strftime("%H:%M:%S")
        if level == "error":
            formatted = f"[{timestamp}] <font color='red'>错误: {message}</font>"
        elif level == "warning":
            formatted = f"[{timestamp}] <font color='orange'>警告: {message}</font>"
        elif level == "success":
            formatted = f"[{timestamp}] <font color='green'>成功: {message}</font>"
        elif level == "info":
            formatted = f"[{timestamp}] <font color='blue'>信息: {message}</font>"
        else:
            formatted = f"[{timestamp}] {message}"
            
        self.status_text.append(formatted)
        scrollbar = self.status_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
    def close_application(self):
        """关闭应用"""
        if self.hotkey_manager:
            self.hotkey_manager.stop()
            
        if hasattr(self, 'tray_icon'):
            self.tray_icon.hide()
            
        QApplication.quit()
        
    def closeEvent(self, event):
        """关闭事件"""
        reply = QMessageBox.question(
            self, '确认退出',
            "是否最小化到系统托盘？\n选择'是'将最小化到托盘，选择'否'将退出应用。",
            QMessageBox.StandardButton.Yes | 
            QMessageBox.StandardButton.No | 
            QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                "",
                "应用已最小化到托盘，按Ctrl+O翻译当前窗口",
                QSystemTrayIcon.MessageIcon.NoIcon,
                3000
            )
        elif reply == QMessageBox.StandardButton.No:
            self.close_application()
            event.accept()
        else:
            event.ignore()


def main():
    """主函数"""
    app = QApplication(sys.argv)
    app.setApplicationName("Huh")
    app.setApplicationDisplayName("Huh")
    
    # 创建必要的目录
    for directory in ["screenshots", "translation_screenshots", "test_screenshots", "temp_screenshots"]:
        if not os.path.exists(directory):
            os.makedirs(directory)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()