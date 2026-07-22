"""Huh 依赖安装脚本 — 双击运行即可安装全部依赖。"""
import sys
import subprocess

_ALL_DEPS = ["requests", "Pillow", "PyQt6", "pyautogui", "pygetwindow", "keyboard"]

deps = sys.argv[1:] if len(sys.argv) > 1 else _ALL_DEPS

print(f"正在安装: {' '.join(deps)}\n")
try:
    subprocess.check_call([sys.executable, "-m", "pip", "install"] + deps)
    print("\n安装完成！可以重新启动 Huh 了。")
except subprocess.CalledProcessError as e:
    print(f"\n安装失败 (exit code {e.returncode})")
    print("请手动运行: pip install", " ".join(deps))

input("\n按 Enter 退出...")
