import serial
import time
import requests
import re

# ================= 配置区 =================
# 1. 串口号：请在设备管理器中确认蓝牙配对后的“传出”端口号（通常是序号较小的那个）
SERIAL_PORT = "COM10" 
# 2. 波特率：必须与单片机 BlueSerial_Init 中的 115200 匹配
BAUD_RATE = 9600
# 3. API 配置
API_KEY = "你自己的API" 
API_URL = "https://api.deepseek.com/v1/chat/completions"
# ==========================================

class BinaryTuner:
    def __init__(self):
        self.p_low = 0.0
        self.p_high = 30.0   
        self.p_curr = 2.0    
        self.stage = "PROBE" 
        self.stable_confirm_count = 0 

    def get_next_p(self, is_bad_state):
        if not is_bad_state: # 只有真正平衡了才尝试缩减区间
            self.p_low = self.p_curr
            if self.stage == "PROBE":
                self.p_curr += 2.0
            else:
                self.p_curr = (self.p_low + self.p_high) / 2
        else: # 震荡或者手扶（力不够/杂波）
            if self.p_curr > self.p_low:
                self.p_high = self.p_curr
            self.stage = "FINE"
            self.p_curr = (self.p_low + self.p_high) / 2
        return round(self.p_curr, 2)

def ask_ai_referee(data_history):
    """
    深度波形分析：区分【受控振荡】、【手扶杂波】、【真平衡】
    """
    prompt = f"""
    你现在是 PID 波形辨识专家。数据格式 (A:角度, V:角速度, O:输出)。
    请分析这组 60 组时序数据：
    {data_history}
    
    判定逻辑：
    1. 震荡 (判定为 YES): V 项呈现极高频率、幅度巨大的正负交替，说明 P 过大。
    2. 手扶伪平衡 (判定为 YES): V 项有波动但毫无规律，波形杂乱且 A 的变化不随 V 的修正而同步回弹。
    3. 真平衡 (判定为 NO): V 项呈现中低频、具有明显周期性反馈特征，且 A 始终被拉向 0 附近。
    
    只需回复：YES 或 NO。
    """
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
    try:
        res = requests.post(API_URL, json=payload, headers=headers, timeout=10)
        ans = res.json()['choices'][0]['message']['content'].upper()
        return "YES" in ans
    except Exception as e:
        print(f"\nAI 接口连接异常: {e}")
        return True # 异常时保守对待，不更新区间

def main():
    tuner = BinaryTuner()
    try:
        # 针对蓝牙优化：增加 write_timeout 防止无线链路堵塞导致脚本卡死
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1, write_timeout=2)
        print(f"--- 模式：纯蓝牙无线调参 ({SERIAL_PORT}) ---")
        print(f"--- 逻辑：识别手扶杂波 & 二分搜索 ---")
        
        buffer = []
        while True:
            if ser.in_waiting:
                # 蓝牙传输可能存在乱码，使用 ignore 忽略非法字节
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                
                # 只有包含 A: V: O: 的行才是我们要的特征行
                if "A:" in line and "V:" in line and "O:" in line:
                    buffer.append(line)
                    if len(buffer) % 15 == 0: print(">", end="", flush=True) 
                
                if len(buffer) >= 60:
                    print("\n[AI 正在分析波形...]")
                    is_bad = ask_ai_referee("\n".join(buffer))
                    
                    if not is_bad:
                        tuner.stable_confirm_count += 1
                        print(f"状态：符合平衡特征 ({tuner.stable_confirm_count}/3)")
                    else:
                        tuner.stable_confirm_count = 0
                        print("状态：波形震荡或判定为手扶杂波")

                    if tuner.stable_confirm_count >= 3:
                        print(f"🎉 🎉 🎉 最终 P 参数锁定为: {tuner.p_curr}")
                        break

                    new_p = tuner.get_next_p(is_bad)
                    
                    # 重要：末尾必须带 \n，否则单片机 BtRxCounter 无法触发命令就绪
                    cmd = f"P={new_p},I=0.00,D=3.00\n" 
                    ser.write(cmd.encode('utf-8'))
                    ser.flush() # 确保数据立刻通过蓝牙模块发出
                    
                    print(f"蓝牙发送 -> {cmd.strip()} (当前搜索区间: {tuner.p_low}-{tuner.p_high})")
                    
                    buffer = []
                    # 蓝牙模式下建议预留 1.2s，给无线链路和物理系统足够的反应时间
                    time.sleep(1.2)
                    
    except serial.SerialException as e:
        print(f"\n串口错误: 请确认蓝牙已配对且没被其他程序(如手机助手)占用。")
        print(f"详细信息: {e}")
    except Exception as e:
        print(f"\n程序运行错误: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("串口已关闭")

if __name__ == "__main__":
    main()
