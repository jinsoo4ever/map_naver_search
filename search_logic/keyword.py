from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from common_logic.utils import CommonUtils


class SearchKeyword:
    def __init__(self, driver, config, log=None):
        self.driver = driver
        self.config = config
        self.log = log
        self.sleep_short_sec = int(config.get("sleep_short_sec", 1))
        self.sleep_normal_sec = int(config.get("sleep_normal_sec", 3))
        self.sleep_long_sec = int(config.get("sleep_long_sec", 5))
        self.web_driver_wait_sec = int(config.get("web_driver_wait_sec", 10))

        self.utils = CommonUtils(self.config, self.log)


    def enter_keyword(self, keyword):
        try:
            self.utils.sleep_random("키워드 입력 전, DOM 안정 대기", self.sleep_short_sec) # 간단한 DOM 안정 대기
            input_elem = self.driver.find_element(By.CSS_SELECTOR, ".search_wrap .input_search")
            input_elem.click()  # 포커스를 확실히 줌
            input_elem.send_keys(Keys.CONTROL + "a")  # 전체 선택
            input_elem.send_keys(Keys.BACKSPACE)  # 삭제
            input_elem.send_keys(keyword)
            input_elem.send_keys(Keys.ENTER)
            self.log(f"[입력] '{keyword}' 입력 후 Enter")
        except Exception as e:
            self.log(f"[에러] '{keyword}' 검색 중 오류:", e)
        #self.utils.sleep_random("키워드 입력하고 검색한 후 대기")
