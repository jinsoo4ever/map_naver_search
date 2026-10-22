from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

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
            # 항상 최상위 프레임에서 검색창을 찾는다 (이전 키워드 처리 중 iframe 안에 있었을 수 있음)
            self.driver.switch_to.default_content()
            # page_load_strategy="eager" 라 get() 직후 검색창이 아직 없을 수 있으므로 명시적으로 기다린다
            input_elem = WebDriverWait(self.driver, self.web_driver_wait_sec).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".search_wrap .input_search"))
            )
            input_elem.click()  # 포커스를 확실히 줌
            input_elem.send_keys(Keys.CONTROL + "a")  # 전체 선택
            input_elem.send_keys(Keys.BACKSPACE)  # 삭제
            input_elem.send_keys(keyword)
            input_elem.send_keys(Keys.ENTER)
            self.log(f"[입력] '{keyword}' 입력 후 Enter")
        except Exception as e:
            self.log(f"[에러] '{keyword}' 검색 중 오류:", e)
            raise
        #self.utils.sleep_random("키워드 입력하고 검색한 후 대기")
