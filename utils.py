import logging
import sys
from datetime import datetime
from colorama import Fore, Style, init

# Initialize colorama
init()

# Configure logging
def setup_logging(log_file="discord_tool.log"):
    """
    Sets up logging to both console and file.
    """
    logger = logging.getLogger("DiscordTool")
    logger.setLevel(logging.INFO)
    
    # Check if handlers are already added to avoid duplicates
    if not logger.handlers:
        # File Handler
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"{Fore.RED}Error setting up log file: {e}{Style.RESET_ALL}")

        # Console Handler (Cleaner output for CLI)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.WARNING) # Only warn/error on console defaults, use print for info
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

def print_info(message):
    print(f"{Fore.CYAN}[INFO]{Style.RESET_ALL} {message}")
    logger.info(message)

def print_success(message):
    print(f"{Fore.GREEN}[SUCCESS]{Style.RESET_ALL} {message}")
    logger.info(message)

def print_warning(message):
    print(f"{Fore.YELLOW}[WARNING]{Style.RESET_ALL} {message}")
    logger.warning(message)

def print_error(message):
    print(f"{Fore.RED}[ERROR]{Style.RESET_ALL} {message}")
    logger.error(message)

def parse_date(date_str):
    """
    Parses a date string YYYY-MM-DD into a datetime object.
    Returns None if invalid.
    """
    try:
        from dateutil import parser
        return parser.parse(date_str)
    except Exception:
        return None

def get_snowflake_time(snowflake):
    """
    Converts a Discord snowflake ID to a datetime object.
    """
    if snowflake is None:
        return None
    timestamp = ((int(snowflake) >> 22) + 1420070400000) / 1000
    return datetime.fromtimestamp(timestamp)
