import time
import random

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

class CommonUtils:
    def __init__(self, config, log_func=print):
        self.log = log_func
        self.sleep_normal_sec = int(config.get("sleep_normal_sec", 3))

    def sleep_random(self, reason = None, base_sec=None):
        if base_sec is None:
            base_sec = self.sleep_normal_sec
        #delay = base_sec + round(random.uniform(0, 0.9), 1)
        delay = base_sec + random.choice([i * 0.05 for i in range(0, 9)])
        self.log(f"{delay}초 대기 ({reason})")
        time.sleep(delay)

    def create_chrome_driver(self) -> webdriver.Chrome:
        """
        Automatically download and run Chrome driver
        """
        service = Service(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        # options.add_argument("--start-maximized")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # options.add_argument("--remote-debugging-port=9222")
        # options.add_argument("--headless")

        return webdriver.Chrome(service=service, options=options)
