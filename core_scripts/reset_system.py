import os
import sys
from pathlib import Path

# 设置 Django 环境
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'IndustrialWarningSystem.settings')

import django
django.setup()

from core_scripts.simulate_real_cnc_data import clear_data, create_devices, simulate_sensor_data, print_summary

def main():
    """
    一键重置系统数据脚本：
    1. 清空所有传感器流水、报警日志、设备信息
    2. 重置 Redis 中的产量计数器与快照
    3. 重新创建 25 台初始设备
    4. 生成 7 天历史仿真数据
    """
    print("========================================")
    print("   工业预警系统 - 数据一键重置工具   ")
    print("========================================\n")
    
    try:
        # Step 1: 清空旧数据
        clear_data()
        
        # Step 2: 初始化物理设备
        devices = create_devices()
        
        # Step 3: 生成历史流动负载
        print("正在生成 7 天历史流水，请稍候...")
        sensor_count, alert_count = simulate_sensor_data(devices)
        
        # Step 4: 打印汇总
        print_summary(len(devices), sensor_count, alert_count)
        print("\n✅ 重置完成！系统已恢复至初始干净状态。")
        print("请现在重启 daphne 和 run_realtime_stream 进程。")
        
    except Exception as e:
        print(f"\n❌ 重置过程中出现错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
