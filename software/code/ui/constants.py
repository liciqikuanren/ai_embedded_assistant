"""UI 常量：串口参数选项、编码列表等。"""

ENCODINGS = [
    "UTF-8", "GBK", "GB2312", "GB18030", "Big5",
    "ASCII", "Latin-1", "UTF-16",
]

BAUD_RATES = [
    "300", "600", "1200", "2400", "4800", "9600", "14400", "19200",
    "38400", "57600", "115200", "230400", "460800", "921600",
]

DATA_BITS = ["5", "6", "7", "8"]
STOP_BITS = ["1", "1.5", "2"]

# 显示名 → pyserial 校验位字符
PARITY = {"None": "N", "Odd": "O", "Even": "E", "Mark": "M", "Space": "S"}
PARITY_BY_VALUE = {v: k for k, v in PARITY.items()}

FLOW_CONTROL = ["None", "RTS/CTS", "XON/XOFF"]

# 换行符类型 → 显示名
NEWLINES = {"CRLF": "\\r\\n (CRLF)", "LF": "\\n (LF)", "CR": "\\r (CR)"}
