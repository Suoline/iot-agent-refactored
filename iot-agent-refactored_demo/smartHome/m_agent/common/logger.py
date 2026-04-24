import logging
from logging.handlers import RotatingFileHandler
import os


def get_logger(logger_name: str, log_file_path: str):
    """
    安全的日志器创建函数：强制日志文件放在当前配置文件目录下，拒绝绝对路径/上级路径逃逸

    参数：
    - logger_name: 日志器名称
    - log_file_path: 日志文件的「相对路径/文件名」（仅允许当前配置目录内的路径，禁止绝对路径/上级路径）
    """
    # 1. 获取当前配置文件(xx.py)的绝对路径和所在目录
    current_file_path = os.path.abspath(__file__)
    config_dir = os.path.dirname(current_file_path)

    # 2. 校验传入的log_file_path：禁止绝对路径、禁止上级目录（../）
    if os.path.isabs(log_file_path):
        raise ValueError(f"禁止传入绝对路径！请传入相对路径（基于{config_dir}）")
    if ".." in log_file_path:
        raise ValueError(f"禁止传入包含'../'的路径！日志文件必须放在{config_dir}目录内")

    # 3. 拼接最终路径（强制在config_dir下），并标准化路径（处理./、//等）
    final_log_file_path = os.path.normpath(os.path.join(config_dir, log_file_path))

    # 4. 自动创建日志文件所在的多级目录（关键：处理logs/order/info.log这类多级路径）
    log_dir = os.path.dirname(final_log_file_path)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        print(f"自动创建日志目录：{log_dir}")

    # 5. 调用setup_logger创建日志器
    return setup_logger(logger_name=logger_name, log_file_path=final_log_file_path)


def setup_logger(
        logger_name: str,  # 必传：logger名字
        log_file_path: str,  # 必传：日志文件存储路径（如 "./logs/app.log"）
        console_level: int = logging.INFO,  # 可选：控制台日志级别，默认INFO
        file_level: int = logging.DEBUG,  # 可选：文件日志级别，默认DEBUG
        max_bytes: int = 50 * 1024 * 1024,  # 可选：单个日志文件最大大小，默认50MB
        backup_count: int = 13,  # 可选：日志文件备份数量，默认13个
        log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
        date_format: str = "%Y-%m-%d %H:%M:%S"
):
    """
    配置并返回一个自定义的日志器

    参数说明：
    - logger_name: 日志器名称（如 "user_service"、"order_service"）
    - log_file_path: 日志文件完整路径（如 "./logs/my_app.log"、"/var/log/app/debug.log"）
    - console_level: 控制台输出的日志级别（logging.DEBUG/INFO/WARNING/ERROR/CRITICAL）
    - file_level: 日志文件保存的日志级别
    - max_bytes: 单个日志文件最大字节数（超过则分割）
    - backup_count: 日志文件备份数量
    - log_format: 日志格式字符串
    - date_format: 时间格式字符串
    """
    # 1. 获取/创建指定名称的logger
    logger = logging.getLogger(logger_name)
    # 防止重复添加handler（多次调用setup_logger时避免日志重复输出）
    if logger.handlers:
        return logger

    # 2. 设置logger总级别（需≤处理器级别，否则会被过滤）
    logger.setLevel(min(console_level, file_level))
    logger.propagate = False  # 避免日志向上传递到root logger导致重复输出

    # 3. 确保日志文件所在目录存在（如果路径包含多级目录，自动创建）
    log_dir = os.path.dirname(log_file_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 4. 定义日志格式
    formatter = logging.Formatter(log_format, datefmt=date_format)

    # 5. 创建控制台处理器（输出到屏幕）
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    # 6. 创建文件处理器（输出到指定路径的文件）
    file_handler = RotatingFileHandler(
        filename=log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"  # 确保中文日志不乱码
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)

    # 7. 将处理器添加到logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

def setup_dynamic_indent_logger(logger_name: str, log_file_path: str):
    """
    配置支持动态缩进的日志器，兼顾控制台输出和文件持久化
    """
    # 1. 获取当前配置文件(xx.py)的绝对路径和所在目录
    current_file_path = os.path.abspath(__file__)
    config_dir = os.path.dirname(current_file_path)

    # 2. 校验传入的log_file_path：禁止绝对路径、禁止上级目录（../）
    if os.path.isabs(log_file_path):
        raise ValueError(f"禁止传入绝对路径！请传入相对路径（基于{config_dir}）")
    if ".." in log_file_path:
        raise ValueError(f"禁止传入包含'../'的路径！日志文件必须放在{config_dir}目录内")

    # 3. 拼接最终路径（强制在config_dir下），并标准化路径（处理./、//等）
    final_log_file_path = os.path.normpath(os.path.join(config_dir, log_file_path))

    # 4. 自动创建日志文件所在的多级目录（关键：处理logs/order/info.log这类多级路径）
    log_dir = os.path.dirname(final_log_file_path)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        print(f"自动创建日志目录：{log_dir}")

    # 2. 创建日志器，设置日志级别
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 避免重复输出

    # 3. 定义格式化器：引用自定义缩进属性`indent`，实现动态缩进
    # 格式说明：%(indent)s 会自动替换为传入的缩进字符串，后续跟核心日志消息
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(indent)s%(message)s",  # 关键：%(indent)s 动态缩进
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 4. 配置控制台处理器（输出到终端）
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 5. 配置文件处理器（持久化到日志文件）
    file_handler = logging.FileHandler(final_log_file_path, encoding="utf-8")  # 指定utf-8避免中文乱码
    file_handler.setFormatter(formatter)

    # 6. 给日志器添加处理器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


# ---------------------- 测试示例 ----------------------
if __name__ == "__main__":
    # 合法调用：传入文件名（推荐）
    logger1 = get_logger("test1", "app.log")
    # 合法调用：传入单层子目录
    logger2 = get_logger("test2", "logs/order.log")
    # logger1.info("try1")
    # logger2.info("try2")
    # 非法调用：传入绝对路径（会抛出异常并提示）
    # logger3 = get_logger("test3", "/tmp/order.log")
    # 非法调用：传入上级路径（会抛出异常并提示）
    # logger4 = get_logger("test4", "../logs/app.log")
    nested_logger=setup_dynamic_indent_logger(logger_name="dynamic_indent_logger",log_file_path="logs/nested_agent.log")
    nested_logger.info("主Agent：开始执行嵌套任务", extra={"indent": ""})
    # 层级1：4个空格缩进
    nested_logger.info("子Agent：接收主Agent任务，准备调用模型", extra={"indent": "    "})
    # 层级2：8个空格缩进
    nested_logger.info("孙子Agent：接收子Agent任务，执行工具调用", extra={"indent": "        "})


