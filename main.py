#!/usr/bin/env python3

import os
import time
import psutil
import subprocess
import logging
import shutil
import argparse
from logging.handlers import RotatingFileHandler
from pathlib import Path

def parse_arguments():
    """Парсинг аргументов командной строки"""
    parser = argparse.ArgumentParser(description='Монитор использования свопа с динамическим расширением')

    parser.add_argument('--max-swaps', type=int, default=3,
                        help='Максимальное количество дополнительных своп-файлов (по умолчанию: 3)')

    parser.add_argument('--swap-size', type=int, default=4096,
                        help='Размер каждого дополнительного своп-файла в МБ (по умолчанию: 4096 МБ)')

    parser.add_argument('--warning-threshold', type=int, default=70,
                        help='Порог предупреждения в процентах (по умолчанию: 70%%)')

    parser.add_argument('--optimize-threshold', type=int, default=85,
                        help='Порог оптимизации в процентах (по умолчанию: 85%%)')

    parser.add_argument('--expand-threshold', type=int, default=95,
                        help='Порог расширения в процентах (по умолчанию: 95%%)')

    parser.add_argument('--log-file', type=str, default='swap_monitor.log',
                        help='Путь к файлу журнала (по умолчанию: swap_monitor.log)')

    parser.add_argument('--swap-base-path', type=str, default='/swapfile.additional',
                        help='Базовый путь для создания дополнительных своп-файлов (по умолчанию: /swapfile.additional)')

    parser.add_argument('--check-interval', type=int, default=10,
                        help='Интервал проверки в секундах (по умолчанию: 10)')

    parser.add_argument('--log-level', type=int, choices=[0, 1, 2], default=1,
                        help='Уровень логирования: 0 — всё в консоль и файл, 1 — файл полностью, консоль только ключевые события, 2 — только файл (по умолчанию: 1)')

    return parser.parse_args()

args = parse_arguments()

# Настройка логирования
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

file_handler = RotatingFileHandler(args.log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
if args.log_level == 0:
    console_handler.setLevel(logging.DEBUG)
elif args.log_level == 1:
    console_handler.setLevel(logging.INFO)
else:
    console_handler.setLevel(logging.CRITICAL)
console_handler.setFormatter(file_formatter)
logger.addHandler(console_handler)

SWAP_WARNING_THRESHOLD = args.warning_threshold
SWAP_OPTIMIZE_THRESHOLD = args.optimize_threshold
SWAP_EXPAND_THRESHOLD = args.expand_threshold

SWAP_FILE_PATH = "/swapfile"
SWAP_EXPAND_SIZE_MB = args.swap_size
SWAP_TEMP_PATH = args.swap_base_path

MAX_ADDITIONAL_SWAPS = args.max_swaps
CHECK_INTERVAL = args.check_interval

def get_memory_info():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        'mem_percent': mem.percent,
        'mem_used': mem.used / (1024 * 1024),
        'mem_total': mem.total / (1024 * 1024),
        'swap_percent': swap.percent,
        'swap_used': swap.used / (1024 * 1024),
        'swap_total': swap.total / (1024 * 1024)
    }

def optimize_swap():
    logging.warning("Начинаем оптимизацию использования памяти...")
    try:
        subprocess.run(["sudo", "sysctl", "vm.drop_caches=1"], check=True)
        subprocess.run(["sudo", "sysctl", "vm.swappiness=10"], check=True)
        subprocess.run(["sudo", "sync"], check=True)
        subprocess.run(["sudo", "bash", "-c", "echo 3 > /proc/sys/vm/drop_caches"], check=False)
        logging.info("Оптимизация памяти выполнена")
        return True
    except subprocess.SubprocessError as e:
        logging.error(f"Ошибка при оптимизации памяти: {e}")
        return False

def count_additional_swaps():
    return sum(os.path.exists(f"{SWAP_TEMP_PATH}{i}") for i in range(1, MAX_ADDITIONAL_SWAPS + 1))

def create_additional_swap():
    additional_count = count_additional_swaps()
    if additional_count >= MAX_ADDITIONAL_SWAPS:
        logging.warning(f"Достигнуто максимальное количество дополнительных своп-файлов ({MAX_ADDITIONAL_SWAPS})")
        return False

    new_swap_path = f"{SWAP_TEMP_PATH}{additional_count + 1}"
    logging.warning(f"Создаем дополнительный своп-файл: {new_swap_path} размером {SWAP_EXPAND_SIZE_MB} МБ")

    try:
        disk_usage = shutil.disk_usage(os.path.dirname(new_swap_path))
        if disk_usage.free / (1024 * 1024) < SWAP_EXPAND_SIZE_MB + 500:
            logging.error("Недостаточно места на диске для создания свопа")
            return False

        subprocess.run(["sudo", "dd", "if=/dev/zero", f"of={new_swap_path}", "bs=1M", f"count={SWAP_EXPAND_SIZE_MB}"], check=True)
        subprocess.run(["sudo", "chmod", "600", new_swap_path], check=True)
        subprocess.run(["sudo", "mkswap", new_swap_path], check=True)
        subprocess.run(["sudo", "swapon", new_swap_path], check=True)

        logging.info(f"Дополнительный своп {new_swap_path} создан и активирован")
        return True
    except subprocess.SubprocessError as e:
        logging.error(f"Ошибка при создании дополнительного свопа: {e}")
        if os.path.exists(new_swap_path):
            subprocess.run(["sudo", "rm", new_swap_path], check=False)
        return False

def remove_additional_swaps():
    found = False
    for i in range(1, MAX_ADDITIONAL_SWAPS + 1):
        swap_path = f"{SWAP_TEMP_PATH}{i}"
        if os.path.exists(swap_path):
            found = True
            try:
                logging.info(f"Отключение дополнительного свопа: {swap_path}")
                result = subprocess.run(["swapon", "--show"], capture_output=True, text=True)
                if swap_path in result.stdout:
                    subprocess.run(["sudo", "swapoff", swap_path], check=True)
                subprocess.run(["sudo", "rm", swap_path], check=True)
                logging.info(f"Дополнительный своп {swap_path} удален")
            except subprocess.SubprocessError as e:
                logging.error(f"Ошибка при удалении своп-файла {swap_path}: {e}")
    return found

def monitor_swap():
    logging.info("Запуск мониторинга свопа")
    logging.info(f"Параметры: MAX={MAX_ADDITIONAL_SWAPS}, SIZE={SWAP_EXPAND_SIZE_MB} МБ, INTERVAL={CHECK_INTERVAL} сек")

    if args.log_level < 2:
        print(f"\nЗапущен мониторинг свопа с лог уровнем {args.log_level}")

    high_usage_counter = 0
    low_usage_counter = 0

    try:
        while True:
            info = get_memory_info()

            # Принудительная выгрузка при >90% RAM
            if info['mem_percent'] >= 90:
                logging.warning(f"RAM загружена на {info['mem_percent']:.1f}%. Принудительный сброс кэша и увеличение swappiness...")
                try:
                    subprocess.run(["sudo", "sysctl", "-w", "vm.swappiness=100"], check=True)
                    subprocess.run(["sudo", "sync"], check=True)
                    subprocess.run(["sudo", "bash", "-c", "echo 3 > /proc/sys/vm/drop_caches"], check=True)
                    logging.info("Принудительная попытка выгрузки из RAM в swap выполнена")
                except subprocess.SubprocessError as e:
                    logging.error(f"Ошибка при попытке выгрузки: {e}")

            swap_percent = info['swap_percent']

            msg = (f"Память: {info['mem_percent']:.1f}% ({info['mem_used']:.0f}/{info['mem_total']:.0f} МБ), "
                   f"Своп: {swap_percent:.1f}% ({info['swap_used']:.0f}/{info['swap_total']:.0f} МБ)")

            if swap_percent >= SWAP_EXPAND_THRESHOLD:
                logging.critical(f"КРИТИЧЕСКОЕ использование свопа: {swap_percent:.1f}%")
                high_usage_counter += 1
                if high_usage_counter >= 3:
                    create_additional_swap()
                    high_usage_counter = 0

            elif swap_percent >= SWAP_OPTIMIZE_THRESHOLD:
                logging.warning(f"ВЫСОКОЕ использование свопа: {swap_percent:.1f}%")
                high_usage_counter += 1
                low_usage_counter = 0
                if high_usage_counter >= 2:
                    optimize_swap()
                    high_usage_counter = 0

            elif swap_percent >= SWAP_WARNING_THRESHOLD:
                logging.warning(f"ПРЕДУПРЕЖДЕНИЕ - {msg}")
                high_usage_counter = 0
                low_usage_counter = 0

            else:
                logging.info(msg)
                high_usage_counter = 0
                low_usage_counter += 1
                if low_usage_counter >= 15:
                    if remove_additional_swaps():
                        logging.info("Удалены дополнительные своп-файлы из-за низкого использования")
                    low_usage_counter = 0

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logging.info("Мониторинг остановлен пользователем")
    except Exception as e:
        logging.error(f"Ошибка в процессе мониторинга: {e}")

def print_current_swap_info():
    info = get_memory_info()
    print(f"""
    === Текущая информация о памяти ===
    Оперативная память: {info['mem_percent']:.1f}% использовано ({info['mem_used']:.0f} МБ из {info['mem_total']:.0f} МБ)
    Своп: {info['swap_percent']:.1f}% использовано ({info['swap_used']:.0f} МБ из {info['swap_total']:.0f} МБ)

    Дополнительные своп-файлы: {count_additional_swaps()} из {MAX_ADDITIONAL_SWAPS}
    """)

if __name__ == "__main__":
    if psutil.swap_memory().total == 0:
        logging.error("Swap не настроен в системе!")
        print("Ошибка: Swap не настроен в системе!")
    else:
        print_current_swap_info()
        monitor_swap()
