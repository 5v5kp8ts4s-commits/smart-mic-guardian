"""
error_handler.py - 全链路异常捕获模块

覆盖范围：
1. 硬件异常：麦克风占用、插拔、无设备
2. 代码异常：音频帧解析出错、ASR 空音频、文件读写异常
3. 运行异常：内存溢出警告、依赖库加载失败
"""

import sys
import os
import threading
import traceback
import time
from typing import Optional, Callable

# 内存监控可选依赖
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

from config import MEMORY_WARN_MB, MEMORY_CLEAR_MB, DEVICE_RETRY_SECS


# ==============================================================
# 异常类型定义
# ==============================================================

class HardwareError(Exception):
    """硬件异常：麦克风占用、插拔、无设备"""
    pass

class AudioFrameError(Exception):
    """代码异常：音频帧解析出错"""
    pass

class ASRError(Exception):
    """代码异常：ASR 模块异常（空音频、模型不可用等）"""
    pass

class FileIOError(Exception):
    """代码异常：文件读写异常"""
    pass

class MemoryWarning(Warning):
    """运行异常：内存占用过高警告"""
    pass

class DependencyError(Exception):
    """运行异常：依赖库加载失败"""
    pass


# ==============================================================
# 全局错误日志（线程安全）
# ==============================================================

class ErrorLog:
    """线程安全的运行期错误记录器"""

    def __init__(self, max_entries: int = 500):
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._max_entries = max_entries
        self._error_counts: dict[str, int] = {}

    def record(self, category: str, message: str, exc: Optional[Exception] = None) -> None:
        """
        记录一条错误。
        :param category: 错误类别（hardware/audio_frame/asr/file_io/memory/dependency/unknown）
        :param message:  可读描述
        :param exc:      原始异常对象（可选）
        """
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "message": message,
            "traceback": traceback.format_exc() if exc else "",
        }
        with self._lock:
            if len(self._entries) >= self._max_entries:
                self._entries.pop(0)  # 丢弃最旧记录
            self._entries.append(entry)
            self._error_counts[category] = self._error_counts.get(category, 0) + 1

        print(f"[错误|{category}] {message}")

    def summary(self) -> dict:
        """返回各类型错误计数摘要"""
        with self._lock:
            return dict(self._error_counts)

    def recent(self, n: int = 10) -> list[dict]:
        """返回最近 n 条错误记录"""
        with self._lock:
            return list(self._entries[-n:])

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._error_counts.clear()


# 全局单例
_error_log = ErrorLog()

def get_error_log() -> ErrorLog:
    return _error_log


# ==============================================================
# 硬件异常处理
# ==============================================================

def handle_hardware_error(exc: Exception, retry_callback: Optional[Callable] = None) -> None:
    """
    处理麦克风硬件异常。
    :param exc: 捕获到的硬件异常
    :param retry_callback: 可选，延迟 DEVICE_RETRY_SECS 秒后调用的重试函数
    """
    _error_log.record("hardware", f"硬件异常: {exc}", exc)

    if retry_callback is not None:
        def _retry():
            time.sleep(DEVICE_RETRY_SECS)
            print(f"[错误处理] {DEVICE_RETRY_SECS:.0f}s 后重试设备连接...")
            try:
                retry_callback()
            except Exception as e:
                _error_log.record("hardware", f"重试失败: {e}", e)
        threading.Thread(target=_retry, daemon=True).start()


def check_audio_device_available() -> bool:
    """
    检查是否有可用的音频输入设备。
    :return: True 有可用设备，False 无
    """
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        has_input = False
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                has_input = True
                break
        pa.terminate()
        if not has_input:
            _error_log.record("hardware", "未检测到可用音频输入设备")
        return has_input
    except ImportError:
        _error_log.record("dependency", "PyAudio 未安装，无法检测音频设备")
        return False
    except Exception as e:
        _error_log.record("hardware", f"设备检测异常: {e}", e)
        return False


# ==============================================================
# 代码异常处理
# ==============================================================

def handle_audio_frame_error(exc: Exception, frame_index: int = -1) -> tuple[str, float]:
    """
    处理音频帧解析异常，返回安全默认值以维持主循环继续运行。
    :return: ('silence', 0.0) 跳过当前帧
    """
    msg = f"音频帧[{frame_index}]解析出错: {exc}"
    _error_log.record("audio_frame", msg, exc)
    return "silence", 0.0


def handle_asr_error(exc: Exception, context: str = "") -> str:
    """
    处理 ASR 模块异常，返回空字符串以不影响主流程。
    :return: "" 跳过当前识别
    """
    msg = f"ASR 异常{f'（{context}）' if context else ''}: {exc}"
    _error_log.record("asr", msg, exc)
    return ""


def handle_file_io_error(exc: Exception, file_path: str = "") -> None:
    """处理文件读写异常（记录日志，不中断主程序）"""
    msg = f"文件读写异常{f'（{file_path}）' if file_path else ''}: {exc}"
    _error_log.record("file_io", msg, exc)


# ==============================================================
# 运行异常处理
# ==============================================================

def check_memory_usage(cleanup_callback: Optional[Callable] = None) -> float:
    """
    检查当前进程内存占用（MB），必要时触发清理回调。
    :param cleanup_callback: 内存超过 MEMORY_CLEAR_MB 时调用的清理函数
    :return: 当前内存占用（MB），不可用时返回 -1
    """
    if not _PSUTIL_AVAILABLE:
        return -1.0

    try:
        proc = psutil.Process(os.getpid())
        mem_mb = proc.memory_info().rss / 1024 / 1024

        if mem_mb >= MEMORY_CLEAR_MB:
            _error_log.record(
                "memory",
                f"内存占用 {mem_mb:.1f}MB 超过清理阈值 {MEMORY_CLEAR_MB}MB，触发清理"
            )
            if cleanup_callback:
                cleanup_callback()
        elif mem_mb >= MEMORY_WARN_MB:
            _error_log.record(
                "memory",
                f"内存占用 {mem_mb:.1f}MB 超过警告阈值 {MEMORY_WARN_MB}MB"
            )

        return mem_mb
    except Exception as e:
        _error_log.record("memory", f"内存检测异常: {e}", e)
        return -1.0


def handle_dependency_error(lib_name: str, exc: Exception) -> None:
    """
    处理依赖库加载失败，打印安装提示。
    :param lib_name: 库名（如 'vosk', 'pyaudio'）
    :param exc: 导入异常
    """
    install_hints = {
        "vosk":    "pip install vosk",
        "pyaudio": "pip install pyaudio",
        "psutil":  "pip install psutil",
    }
    hint = install_hints.get(lib_name, f"pip install {lib_name}")
    msg = f"依赖库 [{lib_name}] 加载失败: {exc}。请执行: {hint}"
    _error_log.record("dependency", msg, exc)
    print(f"[安装提示] {hint}")


# ==============================================================
# 全局未捕获异常兜底
# ==============================================================

def install_global_handler() -> None:
    """
    安装全局未捕获异常处理器（主线程 + 子线程）。
    防止未预期异常导致程序静默崩溃。
    """
    def _excepthook(exc_type, exc_value, exc_tb):
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _error_log.record("unknown", f"未捕获异常: {exc_value}\n{tb_str}")
        # 非 KeyboardInterrupt 时继续运行
        if not issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _threading_excepthook(args):
        tb_str = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback
        ))
        _error_log.record("unknown", f"子线程未捕获异常: {args.exc_value}\n{tb_str}")

    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook
    print("[ErrorHandler] 全局异常处理器已安装")
