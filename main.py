import os
import sys
import threading
import configparser

from datetime import datetime
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QTextEdit, QVBoxLayout
from PySide6.QtCore import Qt, Signal
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

# 워커 스레드(셀레니움 실행 스레드)에서 난 예외도 같은 파일에 남긴다
def handle_thread_exception(args):
    handle_exception(args.exc_type, args.exc_value, args.exc_traceback)
threading.excepthook = handle_thread_exception

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
    # 워커 스레드 → GUI 스레드로 로그를 넘기는 시그널.
    # Qt 위젯은 GUI 스레드에서만 만져야 한다. 다른 스레드에서 text_area 를 직접 append 하면
    # 한참 잘 돌다가 갑자기 멈추거나 죽는 원인이 된다.
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.utils = CommonUtils(self.config, self.log)
        self.driver = None

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
        self.log_signal.connect(self.append_log)

        # Layout configuration
        layout = QVBoxLayout()
        layout.addWidget(self.button)
        layout.addWidget(self.text_area)
        self.setLayout(layout)

        self.excel_output_path = os.path.join(
            get_base_path(), f"result_map_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        )

    def log(self, *args):
        """어느 스레드에서 불러도 안전. 실제 위젯 갱신은 append_log 에서 GUI 스레드가 한다."""
        message = " ".join(str(arg) for arg in args)
        print(message)

        max_length = 200
        trimmed_message = message[:max_length] + ('... [truncated]' if len(message) > max_length else '')
        self.log_signal.emit(trimmed_message)

        # 로그 파일에도 저장
        # with open("log.txt", "a", encoding="utf-8") as f:
        #     f.write(message + "\n")

    def append_log(self, message: str):
        self.text_area.append(message)
        self.text_area.moveCursor(QTextCursor.End)  # 👈 마지막 줄로 스크롤 이동

    def open_chrome(self):
        self.button.setEnabled(False)  # 버튼 비활성화
        self.button.setStyleSheet("background-color: lightgray;")  # 버튼 색상 회색으로 변경
        # Run Selenium execution in a separate thread (avoids GUI freezing)
        # daemon=True: 창을 닫으면 작업 스레드도 같이 정리된다 (closeEvent 에서 크롬을 먼저 닫음)
        threading.Thread(target=self.launch_browser, daemon=True).start()

    def closeEvent(self, event):
        # 창을 닫을 때 크롬/크롬드라이버가 좀비 프로세스로 남지 않도록 종료
        self.utils.quit_driver(self.driver)
        self.driver = None
        super().closeEvent(event)

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

                        # 키워드마다 새 탭에서 시작한다.
                        # - 이전 키워드에서 쌓인 탭 메모리를 비운다 (네이버 지도는 항목을 클릭할수록 메모리가 쌓여
                        #   수백 개쯤에서 탭이 "Out of Memory" 로 죽는다)
                        # - 이전 탭이 죽어 있어도 새 탭으로 이어서 진행할 수 있다
                        if i > 0 and not self.utils.open_fresh_tab(self.driver, self.config.get("main_url")):
                            self.log("[WARN] 새 탭 열기 실패, 기존 탭에서 계속합니다.")

                        # 브라우저가 살아 있는지 확인. 죽어 있으면 더 진행할 수 없으므로 중단.
                        if not self.utils.is_driver_alive(self.driver):
                            self.log("[FATAL] 브라우저가 종료되어 작업을 중단합니다. 프로그램을 다시 실행해 주세요.")
                            break

                        search = SearchKeyword(self.driver, self.config, log=self.log)

                        try:
                            search.enter_keyword(keyword)
                            self.utils.sleep_random("검색 결과 로딩 대기")  # 검색 결과 로딩 대기
                        except Exception as e:
                            self.log(f"[ERROR] 키워드 입력 또는 검색 중 예외 발생: {e}")
                            continue

                        if not self.run_list_click(keyword):
                            self.log("[FATAL] 브라우저 세션이 끊겨 작업을 중단합니다. 프로그램을 다시 실행해 주세요.")
                            break

                        self.log(f"[INFO] '{keyword}' 키워드 처리 완료. 다음 키워드로 이동 준비.")
                    self.log(f"############### 정보 수집이 완료 되었습니다. ###############")
                else:
                    self.log("[INFO] list.txt에는 유효한 키워드가 없습니다.")
            else:
                self.log("[FATAL] list.txt 파일을 찾을 수 없습니다.")
        except Exception as e:
            self.log(e, "[FATAL] run_search 예외:")

    def run_list_click(self, keyword) -> bool:
        """
        :return: True(정상 처리 또는 건너뜀), False(브라우저 세션이 끊김)
        """
        try:
            navigator = ResultsPagedListScraper(self.driver, self.config, log=self.log, excel_output_path=self.excel_output_path)
        except Exception as e:
            self.log(f"[WARN] results 클래스 생성 실패: {e}")
            return True

        alive = navigator.click_all_list_items_across_pages(keyword)
        if not alive:
            return False

        # defualt_content() 로 돌아옴
        try:
            self.driver.switch_to.default_content()
        except Exception as e:
            self.log(f"[WARN] driver.switch_to.default_content() 실패: {e}")

        self.log(f"[DONE] 모든 항목 처리 완료. 저장된 엑셀 파일: {self.excel_output_path}")
        return True

if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()
