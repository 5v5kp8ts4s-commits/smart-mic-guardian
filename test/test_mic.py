#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mic.py - 智能防误关麦助手功能测试脚本

测试范围：
- MicController 初始化
- 麦克风静音状态读取与切换
- 麦克风音量读取与设置
- 实时峰值电平读取
- SpeechGuardian 初始化

注意：
- 本脚本仅能在 Windows 10/11 上运行
- 测试静音/音量设置后会自动恢复到原始状态
"""

import sys
import time

# ========== 平台检查 ==========
if sys.platform != "win32":
    print("[跳过] 本测试脚本仅支持 Windows 平台。")
    sys.exit(0)

# ========== 导入待测模块 ==========
try:
    from main import MicController, SpeechGuardian
except ImportError as e:
    print(f"[错误] 导入 main.py 失败: {e}")
    print("[提示] 请确保 test_mic.py 与 main.py 在同一目录下。")
    sys.exit(1)


def test_init() -> MicController:
    """测试 MicController 初始化"""
    print("\n[测试 1/6] MicController 初始化...")
    try:
        mic = MicController()
        print("  ✓ 初始化成功")
        return mic
    except Exception as e:
        print(f"  ✗ 初始化失败: {e}")
        sys.exit(1)


def test_mute_state(mic: MicController) -> bool:
    """测试读取麦克风静音状态"""
    print("\n[测试 2/6] 读取麦克风静音状态...")
    try:
        muted = mic.is_muted()
        state = "静音" if muted else "开麦"
        print(f"  ✓ 当前状态: {state} (is_muted={muted})")
        return muted
    except Exception as e:
        print(f"  ✗ 读取失败: {e}")
        return False


def test_volume_percent(mic: MicController) -> float:
    """测试读取麦克风音量百分比"""
    print("\n[测试 3/6] 读取麦克风音量百分比...")
    try:
        vol = mic.get_volume_percent()
        print(f"  ✓ 当前音量: {vol}%")
        return vol
    except Exception as e:
        print(f"  ✗ 读取失败: {e}")
        return 50.0


def test_peak_level(mic: MicController) -> None:
    """测试读取实时峰值电平"""
    print("\n[测试 4/6] 读取实时峰值电平（持续 3 秒）...")
    peak = mic.get_peak_level()
    if peak < 0:
        print("  ⚠ 实时电平计量接口不可用（部分声卡不支持）")
        return

    print("  请在 3 秒内对着麦克风说话，或保持安静...")
    for i in range(10):
        peak = mic.get_peak_level()
        bar = "█" * max(0, int(peak * 30))
        print(f"  [{i + 1}/10] 峰值: {peak:.4f} {bar:<30}")
        time.sleep(0.3)
    print("  ✓ 峰值读取正常")


def test_mute_toggle(mic: MicController, original_muted: bool) -> None:
    """测试静音/开麦切换，测试结束后恢复原始状态"""
    print("\n[测试 5/6] 测试静音/开麦切换...")
    try:
        # 先切换到与当前相反的状态
        target = not original_muted
        if target:
            mic.mute()
        else:
            mic.unmute()

        time.sleep(0.3)
        new_state = mic.is_muted()
        print(f"  ✓ 切换后状态: {'静音' if new_state else '开麦'} (is_muted={new_state})")

        # 恢复原始状态
        if original_muted:
            mic.mute()
        else:
            mic.unmute()

        time.sleep(0.3)
        restored = mic.is_muted()
        print(f"  ✓ 已恢复原始状态: {'静音' if restored else '开麦'} (is_muted={restored})")
        assert restored == original_muted, "状态恢复失败！"
    except Exception as e:
        print(f"  ✗ 切换测试失败: {e}")


def test_volume_set(mic: MicController, original_vol: float) -> None:
    """测试设置麦克风音量，测试结束后恢复原始值"""
    print("\n[测试 6/6] 测试音量设置...")
    try:
        test_values = [80.0, 50.0]
        for val in test_values:
            mic.set_volume_percent(val)
            time.sleep(0.2)
            current = mic.get_volume_percent()
            print(f"  ✓ 设置 {val}% → 实际读取 {current}%")

        # 恢复原始音量
        mic.set_volume_percent(original_vol)
        time.sleep(0.2)
        restored = mic.get_volume_percent()
        print(f"  ✓ 已恢复原始音量: {restored}%")
        assert abs(restored - original_vol) < 1.0, "音量恢复失败！"
    except Exception as e:
        print(f"  ✗ 音量设置测试失败: {e}")


def test_speech_guardian(mic: MicController) -> None:
    """测试 SpeechGuardian 初始化"""
    print("\n[附加测试] SpeechGuardian 初始化...")
    try:
        guardian = SpeechGuardian(mic)
        print("  ✓ SpeechGuardian 初始化成功")

        # 测试守护线程启动与停止
        guardian.start_guard()
        time.sleep(1.0)
        guardian.stop_guard()
        print("  ✓ 守护线程启动/停止正常")
    except Exception as e:
        print(f"  ✗ SpeechGuardian 初始化失败: {e}")


def main() -> None:
    """主测试入口"""
    print("=" * 50)
    print("  智能防误关麦助手 — 功能测试脚本")
    print("=" * 50)
    print("[提示] 测试过程中麦克风状态会临时改变，")
    print("      测试结束后会自动恢复到原始状态。\n")

    # 记录原始状态，用于最后恢复
    mic = test_init()
    original_muted = test_mute_state(mic)
    original_vol = test_volume_percent(mic)
    test_peak_level(mic)
    test_mute_toggle(mic, original_muted)
    test_volume_set(mic, original_vol)
    test_speech_guardian(mic)

    # 最终兜底恢复
    if mic.is_muted() != original_muted:
        if original_muted:
            mic.mute()
        else:
            mic.unmute()
    mic.set_volume_percent(original_vol)

    print("\n" + "=" * 50)
    print("  ✓ 全部测试完成，麦克风已恢复原始状态")
    print("=" * 50)


if __name__ == "__main__":
    main()