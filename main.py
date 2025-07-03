import os
import sys
import threading
import configparser

from datetime import datetime
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QTextEdit, QVBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from common_logic.utils import CommonUtils
from search_logic.keyword import SearchKeyword
from results.paged_list_scraper import ResultsPagedListScraper

# ❗ 예외 발생 시 로그 파일 기록
import traceback
def handle_exception(exc_type, exc_value, exc_traceback):
    with open("fatal_error.log", "a", encoding="utf-8") as f:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
sys.excepthook = handle_exception

def get_base_path():
    """실행 파일의 실제 경로 기준 (PyInstaller 대응)"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 실행파일인 경우
        return os.path.dirname(sys.executable)
    else:
        # 일반 실행
        return os.path.dirname(os.path.abspath(__file__))

def load_config(filename="key.ini"):
    """현재 실행 파일과 같은 폴더에 있는 key.ini 읽기"""
    config_path = os.path.join(get_base_path(), filename)
    print(f"[CONFIG] Loading from: {config_path}")  # 👈 로그 추가
    config = configparser.ConfigParser()

    if os.path.exists(config_path):
        config.read(config_path, encoding="utf-8")
    else:
        # 기본값 생성
        config["DEFAULT"] = {
            "main_url": "https://python.worldwideweb.net"
            , "title": "타이틀"
            , "main_button": "메인 버튼"
            , "sleep_short_sec": 1
            , "sleep_normal_sec": 3
            , "sleep_long_sec": 5
            , "web_driver_wait_sec": 10
        }
        with open(config_path, "w", encoding="utf-8") as f:
            config.write(f)
        print(f"[INFO] 기본 key.ini 생성됨: {config_path}")
    return dict(config["DEFAULT"])

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.utils = CommonUtils(self.config, self.log)

        self.sleep_short_sec = int(self.config.get("sleep_short_sec", 1))
        self.sleep_normal_sec = int(self.config.get("sleep_normal_sec", 3))
        self.sleep_long_sec = int(self.config.get("sleep_long_sec", 5))
        self.web_driver_wait_sec = int(self.config.get("web_driver_wait_sec", 10))

        self.setWindowTitle(self.config.get("title", "App"))
        self.setFixedSize(720, 480)

        self.button = QPushButton(self.config.get("main_button", "Start"))
        self.button.setCursor(Qt.PointingHandCursor)  # 손모양 커서 적용

        self.button.setStyleSheet("""
            QPushButton {
                background-color: #2980b9;
                color: white;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #3498db;
            }
        """)

        self.button.clicked.connect(self.open_chrome)

        # Text area for log output
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)  # Editing is prohibited.

        # Layout configuration
        layout = QVBoxLayout()
        layout.addWidget(self.button)
        layout.addWidget(self.text_area)
        self.setLayout(layout)

        self.excel_output_path = os.path.join(
            get_base_path(), f"result_map_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        )

    def log(self, *args):
        message = " ".join(str(arg) for arg in args)
        print(message)

        max_length = 200
        trimmed_message = message[:max_length] + ('... [truncated]' if len(message) > max_length else '')
        self.text_area.append(trimmed_message)
        self.text_area.moveCursor(QTextCursor.End)  # 👈 마지막 줄로 스크롤 이동

        # 로그 파일에도 저장
        # with open("log.txt", "a", encoding="utf-8") as f:
        #     f.write(message + "\n")

    def open_chrome(self):
        self.button.setEnabled(False)  # 버튼 비활성화
        self.button.setStyleSheet("background-color: lightgray;")  # 버튼 색상 회색으로 변경
        # Run Selenium execution in a separate thread (avoids GUI freezing)
        #threading.Thread(target=self.launch_browser, daemon=True).start()
        # daemon=False로 설정해 종료 방지
        threading.Thread(target=self.launch_browser, daemon=False).start()


    def launch_browser(self):
        try:
            self.driver = self.utils.create_chrome_driver()

            url = self.config.get("main_url")
            self.driver.get(url)
            self.run_search()
        except Exception as e:
            self.log(f"[FATAL] launch_browser 예외 발생: {e}")

    def run_search(self):
        try:
            filepath = os.path.join(get_base_path(), "list.txt")
            if os.path.exists(filepath):
                with open(filepath, encoding="utf-8") as f:
                    keywords = [line.strip() for line in f if line.strip()]
                if keywords:
                    self.log(f"[검색 시작] 총 {len(keywords)}개 키워드")
                    for i, keyword in enumerate(keywords):
                        self.log(f"\n[STEP {i + 1}] 키워드 입력: {keyword}")

                        try:
                            # ✅ driver가 복구 됐을 수도 있으므로 SearchKeyword 매번 새로 생성
                            search = SearchKeyword(self.driver, self.config, log=self.log)
                        except Exception as e:
                            self.log(f"[WARN] search 클래스 생성 실패 (건너뜀): {e}")
                            continue

                        # 메인 프레임으로 복귀
                        try:
                            self.driver.switch_to.default_content()
                        except Exception as e:
                            self.log(f"[WARN] default_content 복귀 실패: {e}")

                        self.utils.sleep_random("메인 프레임으로 이동", self.sleep_short_sec)

                        try:
                            search.enter_keyword(keyword)
                            self.utils.sleep_random("검색 결과 로딩 대기")  # 검색 결과 로딩 대기
                        except Exception as e:
                            self.log(f"[ERROR] 키워드 입력 또는 검색 중 예외 발생: {e}")
                            continue

                        # ✅ driver 갱신 반영
                        updated_driver = self.run_list_click(keyword)
                        if updated_driver != self.driver:
                            self.driver = updated_driver
                            self.log("[INFO] run_search 내에서 driver 갱신됨.")

                        self.log(f"[INFO] '{keyword}' 키워드 처리 완료. 다음 키워드로 이동 준비.")
                    self.log(f"############### 정보 수집이 완료 되었습니다. ###############")
                else:
                    self.log("[INFO] list.txt에는 유효한 키워드가 없습니다.")
            else:
                self.log("[FATAL] list.txt 파일을 찾을 수 없습니다.")
        except Exception as e:
            self.log(e, "[FATAL] run_search 예외:")

    def run_list_click(self, keyword):
        try:
            navigator = ResultsPagedListScraper(self.driver, self.config, log=self.log, excel_output_path=self.excel_output_path)
        except Exception as e:
            self.log(f"[WARN] results 클래스 생성 실패: {e}")
            return self.driver  # 기존 driver 그대로 유지

        navigator.click_all_list_items_across_pages(keyword)

        # ✅ 드라이버가 중간에 새로 만들어졌을 경우 현재 driver도 동기화
        if navigator.driver != self.driver:
            self.driver = navigator.driver
            self.log("[INFO] MainWindow의 driver가 복구된 driver로 갱신되었습니다.")

        # defualt_content() 로 돌아옴
        try:
            self.driver.switch_to.default_content()
        except Exception as e:
            self.log(f"[WARN] driver.switch_to.default_content() 실패: {e}")

        self.log(f"[DONE] 모든 항목 처리 완료. 저장된 엑셀 파일: {self.excel_output_path}")

        return self.driver  # ✅ 항상 현재 드라이버 반환

if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()