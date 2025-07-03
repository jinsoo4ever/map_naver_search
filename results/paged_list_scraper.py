import gc
import psutil
import re

from bs4 import BeautifulSoup

from selenium.common import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.webdriver import WebDriver  # 타입 힌트용
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common_logic.utils import CommonUtils
from search_logic.keyword import SearchKeyword
from file_logic.excel import append_info_row_to_excel


class ResultsPagedListScraper:
    def __init__(self, driver, config, log=None, excel_output_path=None):
        self.driver = driver
        self.config = config
        self.log = log or print
        self.recover_test_done = False  # 복구 테스트 1회용 플래그

        self.sleep_short_sec = int(config.get("sleep_short_sec", 1))
        self.sleep_normal_sec = int(config.get("sleep_normal_sec", 3))
        self.sleep_long_sec = int(config.get("sleep_long_sec", 5))
        self.web_driver_wait_sec = int(config.get("web_driver_wait_sec", 10))
        self.wait = WebDriverWait(driver, 10)

        self.utils = CommonUtils(self.config, self.log)
        self.excel_output_path = excel_output_path

    def switch_to_frame(self, frame_id: str) -> bool:
        try:
            WebDriverWait(self.driver, self.web_driver_wait_sec).until(
                EC.frame_to_be_available_and_switch_to_it((By.ID, frame_id))
            )

            sleep_sec = self.sleep_short_sec * 1.5 if frame_id == "entryIframe" else self.sleep_short_sec

            self.utils.sleep_random(f"iframe({frame_id}) 진입 완료", sleep_sec)
            return True
        except Exception as e:
            self.log(f"[ERROR] iframe({frame_id}) 진입 실패: {e}")
            return False

    def kill_driver_children(self, parent_pid):
        try:
            parent = psutil.Process(parent_pid)
            for child in parent.children(recursive=True):
                try:
                    if 'chrome' in child.name().lower():
                        child.kill()
                except Exception:
                    pass
        except Exception as e:
            print(f"[경고] Chrome 자식 프로세스 종료 중 오류 발생: {e}")

    def relaunch_browser_and_search_keyword(self, keyword: str) -> WebDriver | None:
        try:
            # 기존 드라이버 종료 및 자식 프로세스 강제 종료
            if self.driver:
                try:
                    chrome_pid = self.driver.service.process.pid
                    self.driver.quit()
                    self.log(f"[INFO] 기존 드라이버 quit 완료 (PID: {chrome_pid})")

                    # ChromeDriver가 생성한 chrome 프로세스 강제 종료
                    self.kill_driver_children(chrome_pid)
                    self.log(f"[INFO] Chrome 자식 프로세스 종료 완료")

                except Exception as e:
                    self.log(f"[경고] 기존 드라이버 종료 중 오류 무시: {e}")
        except Exception:
            pass

        try:
            self.driver = self.utils.create_chrome_driver()
            self.wait = WebDriverWait(self.driver, self.web_driver_wait_sec)

            url = self.config.get("main_url")
            self.driver.get(url)
            self.utils.sleep_random("메인 페이지 로딩 대기", self.sleep_long_sec)

            search = SearchKeyword(self.driver, self.config, log=self.log)
            self.log(f"[복구] 키워드 재입력: {keyword}")
            search.enter_keyword(keyword)
            self.utils.sleep_random("검색 결과 로딩 대기", self.sleep_normal_sec)

            return self.driver  # ✅ 새로 생성된 driver 반환
        except Exception as e:
            self.log(f"[ERROR] 브라우저 재시작 중 예외 발생: {e}")
            return None

    # 복구 함수 정의
    def recover_after_timeout(self, page_number: int, keyword: str) -> bool:
        self.log("[복구] Chrome 드라이버 종료 및 재시작 중...")
        try:
            new_driver = self.relaunch_browser_and_search_keyword(keyword)
            if not new_driver:
                return False

            self.driver = new_driver  # ✅ driver 교체
            self.wait = WebDriverWait(self.driver, self.web_driver_wait_sec)

            # ✅ iframe 진입
            if not self.switch_to_frame("searchIframe"):
                return False

            # ✅ 페이지 이동
            if self.go_to_page(page_number):
                self.utils.sleep_random(f"{page_number}p 페이지 복귀 완료", self.sleep_short_sec)
                return True
            else:
                self.log("[복구 실패] go_to_page 실패")
                return False

        except Exception as e:
            self.log(f"[복구 실패] 전체 복구 루틴 중 예외 발생: {e}")
            return False


    def click_all_list_items_across_pages(self, keyword):
        page_number = 1

        # 1. iframe(서치아이프레임) 으로 진입
        if not self.switch_to_frame("searchIframe"):
            return False

        ## ~~ test (특정 아이템 확인) ~~ ing
        ## self.go_to_page(2)

        while True:
            try:

                self.log(f"[페이지 {page_number}] 리스트 항목 처리 시작")

                # 스크롤 타겟: _pcmap_list_scroll_container
                if not self.scroll_through_list_container():
                    self.log("[오류] 스크롤 중 오류 발생 ")

                # 3. li 목록 가져오기 (페이지마다 동적으로 다시 로드해야 함)
                li_elements = self.wait.until(
                    EC.presence_of_all_elements_located((By.XPATH, '//*[@id="app-root"]//ul[1]/li'))
                )

                titles = [
                    li.find_element(By.CSS_SELECTOR, '.place_bluelink span:first-child').text.strip()
                    for li in li_elements
                ]
                self.log(f"[INFO]{page_number}p {len(titles)}개의 항목 발견됨.")
                # 처음 5개 항목만 로그로 출력 (PySide6에서 긴 문자열 출력이 문제를 일으킴)
                preview_count = 5
                preview_titles = titles[:preview_count]
                self.log(f" └▶ 대표 {preview_count}개: {'|'.join(preview_titles)}")

                index = 0

                ## ~~ test (특정 아이템 확인) ~~ ing
                # try:
                #     index_of_sojeom = titles.index("소점")
                #     index = index_of_sojeom
                #     self.log(f"'소점'의 index: {index_of_sojeom}")
                # except ValueError:
                #     self.log("'소점'이 리스트에 없습니다.")

                while index < len(li_elements):
                    # ✅ ~~ test (페이지 전환 테스트) ~~ ing 테스트용: 3번째(인덱스 3) 항목까지 처리한 뒤 중단
                    # if index >= 3:
                    #    self.log("[TEST] index == 3 도달 → 페이지 처리 테스트 종료")
                    #    break

                    # ~~ test (크롬 드라이버 재실행 테스트) ~~ ing
                    # if (page_number == 4 and index == 0 and not self.recover_test_done) :
                    #     self.recover_test_done = True  # ✅ 한 번만 실행되도록 플래그 설정
                    #     if self.recover_after_timeout(page_number, keyword):
                    #         index = max(index - 1, 0)  # 🔁 이전 항목부터 다시 시작
                    #         continue
                    #     else:
                    #         self.log("[중단] 복구 실패로 페이지 루프 종료")
                    #         break


                    # ✅ 리스트 매 10개마다 메모리 정리
                    if index % 10 == 0:
                        self.log(f"[GC] {index}번째 항목 처리 중, 메모리 정리 수행")
                        gc.collect()

                    try:
                        # 다시 가져와야 stale element 방지됨
                        li_elements = self.driver.find_elements(By.XPATH, '//*[@id="app-root"]//ul[1]/li')
                        target_li = li_elements[index]

                        # 내부의 a 태그 (장소명 링크) 클릭
                        link_elem = target_li.find_element(By.XPATH, './/a[contains(@class, "place_bluelink")]')
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", link_elem)
                        link_elem.click()

                        item = titles[index] if index < len(titles) else "Unknown"
                        self.utils.sleep_random(f"[클릭] {page_number}p {index + 1}번째 항목({item}) 클릭 완료", self.sleep_short_sec)

                        # 상세 정보 가져오기
                        info = self.handle_entry_iframe_and_extract_info()
                        self.log(f"[INFO] : {info}")

                        # 엑셀에 저장
                        append_info_row_to_excel(
                            search_keyword=keyword,
                            page=page_number,
                            index=index,
                            info=info,
                            output_path=self.excel_output_path  # → "result_map_20250612152200.xlsx" 형식 등
                        )

                        index += 1  # 성공 시 다음으로 진행
                        #self.utils.sleep_random("상세 정보 가져오기", self.sleep_short_sec)

                    except Exception as e:
                        self.log(f"[에러] {page_number}p {index + 1}번째 항목 클릭 중:", e)

                        # ⚠️ 복구 가능한 예외 메시지이면 복구 시도
                        if self.is_recoverable_error(e):
                            self.log("[알림] 복구가 필요한 예외 발생 → 복구 루틴 진입")

                            if self.recover_after_timeout(page_number, keyword):
                                index = max(index - 1, 0)  # 🔁 이전 항목부터 다시 시작
                                continue
                            else:
                                self.log("[중단] 복구 실패로 페이지 루프 종료")
                                break
                        else:
                            index += 1  # 그 외 에러는 다음으로 넘어감

                # 다음 페이지 이동 처리
                next_page_result = self.go_to_next_page()
                if next_page_result == 1:
                    page_number += 1
                    self.utils.sleep_random("다음 페이지 이동 처리")
                elif next_page_result == -1:
                    self.log("[알림] 복구가 필요한 예외 발생 → 복구 루틴 진입")

                    if self.recover_after_timeout(page_number, keyword):
                        index = max(index - 1, 0)  # 🔁 이전 항목부터 다시 시작
                        continue
                    else:
                        self.log("[중단] 복구 실패로 페이지 루프 종료")
                        break
                else:
                    self.log("[종료] 더 이상 다음 페이지가 없습니다.")
                    break

            except Exception as e:
                self.log("[에러] 페이지 처리 중 중단:", e)
                if self.is_recoverable_error(e):
                    self.log("[알림] 복구가 필요한 예외 발생 → 복구 루틴 진입")
                    if self.recover_after_timeout(page_number, keyword):
                        index = max(index - 1, 0)  # 🔁 이전 항목부터 다시 시작
                        continue
                    else:
                        self.log("[중단] 복구 실패로 페이지 루프 종료")
                        break
                else:
                    break  # 그 외 일반 예외는 중단

    def is_recoverable_error(self, e: Exception) -> bool:
        """
        브라우저 또는 iframe 세션 오류 등 복구 가능한 예외 메시지가 포함되어 있는지 확인
        """
        msg = str(e)
        return any(keyword in msg for keyword in [
            "Read timed out",
            "target frame detached",  # ✅ iframe이 분리된 경우
            "session not created",    # ✅ 세션 생성 실패
            "invalid session id"      # ✅ 세션이 유효하지 않음
        ])

    def go_to_next_page(self):
        """
        '다음페이지' 버튼을 찾아 클릭하고 성공 여부 반환
        :return: 1(성공적으로 이동), 0(버튼 없음 또는 비활성화), -1(복구 가능한 예외 메시지)
        """
        try:
            next_btn = self.driver.find_element(By.XPATH, '(//a[./span[text()="다음페이지"]])[last()]')
            if next_btn.get_attribute("aria-disabled") == "true":
                return 0
            next_btn.click()
            return 1
        except Exception as e:
            self.log("[WARN] 다음 페이지 버튼 클릭 실패:", e)
            if self.is_recoverable_error(e):
                return -1
            return 0

    def go_to_page(self, page_number: int) -> bool:
        """
        특정 페이지 번호 버튼을 클릭하고 성공 여부를 반환
        :param page_number: 이동하려는 페이지 번호 (예: 2)
        :return: True(성공적으로 이동), False(버튼 없음 또는 클릭 실패)
        """
        try:
            # 페이지 번호가 버튼에 텍스트로 표시되어 있으므로 텍스트 매칭
            page_btn = self.driver.find_element(
                By.XPATH,
                f'//a[@role="button" and normalize-space(text())="{page_number}"]'
            )
            page_btn.click()
            self.utils.sleep_random(f"{page_number}p 페이지 이동")
            self.scroll_through_list_container()

            return True
        except Exception as e:
            self.log(f"[WARN] 페이지 {page_number} 버튼 클릭 실패:", e)
            return False

    def scroll_through_list_container(self):
        try:
            # 스크롤 타겟: _pcmap_list_scroll_container
            scroll_target = self.wait.until(
                EC.presence_of_element_located((By.ID, "_pcmap_list_scroll_container"))
            )

            last_height = self.driver.execute_script("return arguments[0].scrollHeight", scroll_target)
            scroll_tries = 0
            max_tries = 15

            while scroll_tries < max_tries:
                self.driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight);", scroll_target)
                self.utils.sleep_random("스크롤", self.sleep_short_sec)

                new_height = self.driver.execute_script("return arguments[0].scrollHeight", scroll_target)
                if new_height == last_height:
                    break

                last_height = new_height
                scroll_tries += 1

            self.log("[INFO] 스크롤 완료, 전체 항목 로딩 완료됨")
            return True
        except Exception as e:
            self.log(f"[ERROR] 스크롤 중 오류 발생: {e}")
            return False

    def handle_entry_iframe_and_extract_info(self):
        try:
            # 1. 메인 프레임으로 복귀
            self.driver.switch_to.default_content()
            #self.utils.sleep_random("상세정보 추출 준비", self.sleep_short_sec)

            # 2. 엔트리아이프레임 으로 진입
            if not self.switch_to_frame("entryIframe"):
                return False


            # iframe 안에서 렌더링된 내용의 구조를 디버깅하거나 특정 DOM 탐지 오류를 추적
            #html = self.driver.page_source
            #start_index = html.find('<div id="app-root">')

            # 3. 정보 추출
            info = self.extract_info_from_place_section()
            # self.log(f"[INFO] 추출된 정보: {info}")

        except Exception as e:
            self.log(f"[ERROR] (상세) 엔트리아이프레임 진입 또는 정보 추출 실패: {e}")
            info = {'address': None, 'lot_number': None, 'phone': None, 'homepage': None, 'share': None, 'intro': None, 'main_keyword': []}

        finally:
            # 4. 메인 프레임 복귀
            try:
                self.driver.switch_to.default_content()
                #self.utils.sleep_random("리스트로 이동 중", self.sleep_short_sec)
            except Exception as e:
                self.log(f"[ERROR] 메인 프레임 복귀 실패: {e}")

            # 5. 다시 서치아이프레임 으로 진입
            try:
                if not self.switch_to_frame("searchIframe"):
                    return False
                #self.log("[INFO] (리스트) 서치아이프레임 복귀 완료")
            except Exception as e:
                self.log(f"[ERROR] (리스트) 서치아이프레임 복귀 실패: {e}")

        return info

    def extract_info_from_place_section(self):
        max_retry = 2
        outer_retry_count = 0

        while outer_retry_count < max_retry:
            try:
                info = {}

                # ✅ 'place_section'에서 첫 번째 요소에서 학원명 추출
                place_sections = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "place_section"))
                )
                if place_sections:
                    try:
                        # name은 첫 번째 place_section > div > div#_title > div > span[0]
                        name_elem = place_sections[0].find_element(By.XPATH, './/div[@id="_title"]//div/span[1]')
                        info['name'] = name_elem.text.strip()
                    except Exception as e:
                        self.log(f"[WARN] 이름 추출 실패: {e}")
                        info['name'] = None
                else:
                    self.log("[WARN] 'place_section' 요소 없음")
                    info['name'] = None

                # ✅ 'place_section_content' 전체 블록 가져오기
                inner_retry_count = 0
                info_section = None

                while inner_retry_count < max_retry:
                    content_sections = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_all_elements_located((By.CLASS_NAME, "place_section_content"))
                    )

                    if len(content_sections) >= 2:
                        info_section = content_sections[1] # ✅ 두 번째 섹션에서 정보 추출
                        break  # 성공했으니 while 탈출
                    else:
                        self.log(f"[WARN] 두 번째 'place_section_content' 요소를 찾을 수 없습니다. 재시도 {inner_retry_count + 1}/{max_retry}")
                        self.utils.sleep_random("재시도 대기", self.sleep_normal_sec)
                        inner_retry_count += 1

                if not info_section:
                    raise Exception("두 번째 'place_section_content' 요소 최종 실패")

                # 모든 텍스트 추출 & comma+space 적용
                # def get_value_by_label(label_text):
                #     try:
                #         label_elem = info_section.find_element(
                #             By.XPATH, f'.//strong[span[contains(text(), "{label_text}")]]'
                #         )
                #         container_div = label_elem.find_element(By.XPATH, "./following-sibling::div")
                #         html = container_div.get_attribute("outerHTML")
                #         soup = BeautifulSoup(html, "html.parser")
                #
                #         texts = []
                #         for elem in soup.stripped_strings:
                #             if elem not in texts:
                #                 texts.append(elem)
                #
                #         return ", ".join(texts)
                #
                #     except Exception as e:
                #         self.log(f"[WARN] '{label_text}' 정보 추출 실패: {e}")
                #         return None
                # 맨 처음 <span> 태그의 텍스트만 추출
                def get_value_by_label(label_text):
                    try:
                        label_elem = info_section.find_element(
                            By.XPATH, f'.//strong[span[contains(text(), "{label_text}")]]'
                        )
                        container_div = label_elem.find_element(By.XPATH, "./following-sibling::div")
                        html = container_div.get_attribute("outerHTML")
                        soup = BeautifulSoup(html, "html.parser")

                        # 첫 번째 <span> 태그의 텍스트만 추출
                        first_span = soup.find("span")
                        if first_span and first_span.get_text(strip=True):
                            return first_span.get_text(strip=True)
                        else:
                            return None

                    except Exception as e:
                        self.log(f"[WARN] '{label_text}' 정보 추출 실패: {e}")
                        return None

                def click_address_more():
                    try:
                        label_elem = info_section.find_element(
                            By.XPATH, './/strong[span[contains(text(), "주소")]]'
                        )

                        container_div = label_elem.find_element(By.XPATH, './following-sibling::div')

                        # div 내부 첫 번째 a 태그 찾기 (주소 전체 영역)
                        address_link_list = container_div.find_elements(By.XPATH, './/a[1]')
                        if not address_link_list:
                            self.log("[WARN] 주소 전체 영역 a 태그를 찾을 수 없습니다.")
                            return None
                        address_link = address_link_list[0]

                        address_link.click()
                        self.utils.sleep_random("주소 펼침 svg 클릭 완료", self.sleep_short_sec)

                        # 지번 전체 div 찾기
                        lot_number_div_list = container_div.find_elements(By.XPATH, './/div[span[text()="지번"]]')
                        if not lot_number_div_list:
                            self.log("[WARN] 지번 div를 찾을 수 없습니다.")
                            return None
                        lot_number_div = lot_number_div_list[0]

                        # 지번 div의 outerHTML 가져오기
                        html = lot_number_div.get_attribute("outerHTML")
                        soup = BeautifulSoup(html, "html.parser")

                        # '지번' <span>을 찾음
                        label_span = soup.find("span", string="지번")

                        lot_number_value = None

                        if label_span:
                            # '지번' <span>의 다음 텍스트 노드를 순회
                            next_node = label_span.next_sibling
                            while next_node:
                                if isinstance(next_node, str):
                                    lot_number_value = next_node.strip()
                                    break
                                next_node = next_node.next_sibling

                        self.log(f"[INFO] 지번 값 추출 성공: {lot_number_value}")

                        # 안내 레이어 닫기 버튼 클릭
                        close_btn_list = container_div.find_elements(
                            By.XPATH, './/a[span[contains(text(), "안내 레이어 닫기")]]'
                        )
                        if close_btn_list:
                            close_btn_list[0].click()
                            self.utils.sleep_random("안내 레이어 닫기 클릭 완료", self.sleep_short_sec)
                        else:
                            self.log("[WARN] 안내 레이어 닫기 버튼을 찾을 수 없습니다.")

                        return lot_number_value

                    except Exception as e:
                        self.log(f"[WARN] 주소 펼침 또는 지번 추출 실패: {e}")

                # ✅ 주소 추출
                info['address'] = get_value_by_label("주소")

                # ✅ 전화번호 추출 (첫 번째 span만 타겟)
                try:
                    phone_block = info_section.find_element(
                        By.XPATH, './/strong[span[contains(text(), "전화번호")]]/following-sibling::div'
                    )

                    # Case 1: '전화번호 보기' 버튼 존재
                    try:
                        view_btn = phone_block.find_element(By.XPATH, './/a[contains(text(), "전화번호 보기")]')
                        view_btn.click()
                        self.log("[INFO] '전화번호 보기' 버튼 클릭됨")

                        # 기다린 후 <em> 태그 안에 전화번호 추출
                        phone_elem = WebDriverWait(phone_block, 5).until(
                            EC.presence_of_element_located(
                                (By.XPATH, './/em[normalize-space(string()) and contains(text(), "-")]')
                            )
                        )
                        info['phone'] = phone_elem.text.strip()

                    except NoSuchElementException:
                        # Case 2: '전화번호 보기' 버튼이 없고 전화번호가 직접 노출된 경우
                        try:
                            phone_span = phone_block.find_element(By.XPATH, './/span[contains(text(), "-")]')
                            info['phone'] = phone_span.text.strip()
                        except Exception:
                            self.log("[INFO] 전화번호가 직접 노출된 span 없음")
                            info['phone'] = None

                except NoSuchElementException:
                    # Case 3: 전화번호 블록 자체가 없는 경우
                    self.log("[INFO] 전화번호 블록 자체 없음")
                    info['phone'] = None

                except Exception as e:
                    self.log(f"[ERROR] 전화번호 정보 추출 중 예외 발생: {e}")
                    info['phone'] = None

                # 🔍 홈페이지 정보 추출
                try:
                    homepage_label_elems = info_section.find_elements(By.XPATH, './/strong[span[contains(text(), "홈페이지")]]')
                    if homepage_label_elems:
                        homepage_container = homepage_label_elems[0].find_element(By.XPATH, './following-sibling::div')
                        homepage_link_elem = homepage_container.find_element(By.XPATH, './/a')
                        info['homepage'] = homepage_link_elem.get_attribute("href")
                    else:
                        self.log("[INFO] '홈페이지' 항목이 존재하지 않음")
                        info['homepage'] = None
                except NoSuchElementException as e:
                    self.log(f"[INFO] '홈페이지' 링크 없음: {e}")
                    info['homepage'] = None
                except Exception as e:
                    self.log(f"[ERROR] '홈페이지' 정보 추출 중 예상치 못한 오류: {e}")
                    info['homepage'] = None

                # 📤 공유 정보 추출
                try:
                    # XPath: place_section의 첫 번째 요소의 하위 3번째 div > 마지막 span > a
                    xpath = '(//div[contains(@class, "place_section")])[1]//div[3]//span[last()]//a'
                    link_elem = self.driver.find_element(By.XPATH, xpath)
                    link_elem.click()
                    self.utils.sleep_random("공유 클릭", self.sleep_short_sec)
                    last_a_elem = self.driver.find_element(By.XPATH, '//div[contains(@class, "spi_copyurl")]/a[last()]')
                    last_href = last_a_elem.get_attribute("href")
                    print(f"[INFO] 공유 url: {last_href}")
                    info['share'] = last_href

                    # 닫기
                    try:
                        copyurl_elem = self.driver.find_element(By.CLASS_NAME, "spi_copyurl")
                        close_btn = copyurl_elem.find_element(By.XPATH, "./following-sibling::a[contains(@class, '_spi_cls')]")
                        if close_btn.is_displayed() and close_btn.is_enabled():
                            close_btn.click()
                        else:
                            self.log("[WARN] 공유 닫기 버튼 비활성화")
                    except Exception as e:
                        self.log(f"[WARN] 공유 닫기 버튼 클릭 실패: {e}")

                except Exception as e:
                    self.log(f"[WARN] 공유 추출 실패: {e}")
                    info['share'] = None

                # 지번 주소(lot_number) 추출
                ## ~~ ing
                info['lot_number'] = click_address_more()

                # ⋮ (more) 정보 더보기
                self.extract_detail_info_if_more_available(info_section, info)

                return info # 성공했으니 바로 return

            except Exception as e:
                self.log(f"[ERROR] 전체 섹션 로딩 실패 (시도 {outer_retry_count + 1}/{max_retry}): {e}")
                outer_retry_count += 1
                self.utils.sleep_random("전체 섹션 재시도 대기", self.sleep_normal_sec)
        # 최종 실패
        self.log("[ERROR] 전체 섹션 로딩 최종 실패")
        return {'address': None, 'lot_number': None, 'phone': None, 'homepage': None, 'share': None, 'intro': None, 'main_keyword': []}


    def extract_detail_info_if_more_available(self, info_section, info):
        try:
            # (1) 정보 더보기 버튼 (span 텍스트에 "정보" 포함 여부)
            more_div = info_section.find_element(By.XPATH, './following-sibling::div[1]')
            span_texts = [span.text.strip() for span in more_div.find_elements(By.TAG_NAME, 'span')]
            has_info_button = any("정보" in text for text in span_texts)

            if not has_info_button:
                self.log("[INFO] '정보 더보기' 버튼이 없음. 상세 정보 추출 생략.")
                return

            # (2) a 태그 클릭
            more_links = more_div.find_elements(By.TAG_NAME, 'a')
            if more_links and more_links[0].is_displayed() and more_links[0].is_enabled():
                more_links[0].click()
                self.utils.sleep_random("(more) 정보 더보기 클릭 완료", self.sleep_short_sec)
            else:
                self.log("[WARN] (more) 정보 더보기 링크는 비활성 또는 없음")
                return

            # (3) 소개 텍스트 추출
            try:
                intro_elem = self.driver.find_element(
                    By.XPATH,
                    '(//div[contains(@class, "place_section_content")])[1]/div[last()]/div[last()]/div[last()]'
                )
                info['intro'] = intro_elem.text.strip()
                #self.log(f"[INFO] 소개 텍스트 추출 완료: {info['intro']}")
            except Exception as e:
                self.log(f"[WARN] 소개 텍스트 추출 실패: {e}")
                info['intro'] = None

            # (4) 대표 키워드 추출
            try:
                keyword_title_elems = self.driver.find_elements(
                    By.XPATH,
                    '//div[contains(@class, "place_section_header_title") and contains(text(), "대표 키워드")]'
                )

                if keyword_title_elems:
                    keyword_title_elem = keyword_title_elems[0]
                    keyword_content_elem = keyword_title_elem.find_element(
                        By.XPATH,
                        './../following-sibling::div[contains(@class, "place_section_content")]'
                    )
                    span_elems = keyword_content_elem.find_elements(By.XPATH, './/span')
                    keywords = [span.text.strip() for span in span_elems if span.text.strip()]
                    info['main_keyword'] = keywords
                    #self.log(f"[INFO] 대표 키워드 추출 완료: {info['main_keyword']}")
                else:
                    self.log("[INFO] 대표 키워드 섹션 없음")
                    info['main_keyword'] = []
            except Exception as e:
                self.log(f"[WARN] 대표 키워드 추출 실패: {e}")
                info['main_keyword'] = []

            # (5) 닫기 버튼 클릭
            try:
                close_btn = self.driver.find_element(
                    By.XPATH,
                    '//header[contains(@class, "place_tab_shadow")]//a[span[text()="페이지 닫기"]]'
                )
                if close_btn.is_displayed() and close_btn.is_enabled():
                    close_btn.click()
                    #self.log("[INFO] 페이지 닫기 버튼 클릭 완료")
                else:
                    self.log("[WARN] 닫기 버튼이 비활성화 상태입니다.")
            except Exception as e:
                self.log(f"[ERROR] 닫기 버튼 클릭 실패: {e}")

        except Exception as e:
            self.log(f"[ERROR] '정보 더보기' 처리 중 예외 발생: {e}")