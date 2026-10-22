import gc
import re

from bs4 import BeautifulSoup

from selenium.common import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common_logic.utils import (
    CommonUtils, is_session_dead_error, is_frame_lost_error, is_renderer_dead_error,
)
from search_logic.keyword import SearchKeyword
from file_logic.excel import append_info_row_to_excel


class BrowserSessionDead(Exception):
    """브라우저/드라이버 세션이 완전히 끊겨 더 이상 진행할 수 없을 때"""


class BrowserTabDead(Exception):
    """탭(렌더러)이 죽어 응답하지 않을 때. 새 탭으로 갈아타면 계속할 수 있다 (예: Out of Memory)"""


# 상세 정보 추출에 실패했을 때 엑셀에 빈 행이라도 남기기 위한 기본값
EMPTY_INFO = {'address': None, 'lot_number': None, 'phone': None, 'homepage': None, 'share': None, 'intro': None, 'main_keyword': []}


class ResultsPagedListScraper:
    # 같은 항목에서 프레임 재진입을 몇 번까지 시도할지
    MAX_FRAME_RECOVER_TRIES = 3
    # 같은 페이지에서 탭 교체(새 탭 재검색)를 몇 번까지 시도할지
    MAX_TAB_RECOVER_TRIES = 3

    # 리스트 항목(li) 안의 가게명 링크.
    # 예전 DOM 은 a.place_bluelink 였지만 지금은 난독화 클래스(XCvzh, YTJkH ...)라 클래스에 기대지 않고,
    # 썸네일 링크(a.place_thumb)를 제외한 첫 번째 role="button" 링크를 가게명 링크로 본다.
    TITLE_LINK_XPATH = ('.//a[contains(@class, "place_bluelink")]'
                        ' | .//a[@role="button" and not(contains(@class, "place_thumb"))]')

    # 상세 화면 제목 영역에 가게명보다 먼저 나올 수 있는 배지 문구. 이 값이 잡히면 가게명이 아니다.
    NAME_BADGE_TEXTS = ("플레이스 플러스", "플레이스플러스")

    def __init__(self, driver, config, log=None, excel_output_path=None):
        self.driver = driver
        self.config = config
        self.log = log or print

        self.sleep_short_sec = int(config.get("sleep_short_sec", 1))
        self.sleep_normal_sec = int(config.get("sleep_normal_sec", 3))
        self.sleep_long_sec = int(config.get("sleep_long_sec", 5))
        self.web_driver_wait_sec = int(config.get("web_driver_wait_sec", 10))
        self.wait = WebDriverWait(driver, self.web_driver_wait_sec)

        self.utils = CommonUtils(self.config, self.log)
        self.excel_output_path = excel_output_path
        # 한 탭에서 항목을 몇 개 처리할 때마다 새 탭으로 갈아탈지 (key.ini 의 tab_reset_every_items 로 조정 가능).
        # 실측: 항목 50개 처리에 JS 힙 1.7GB, 문서 148개까지 쌓임 → 20개마다 비우면 넉넉하다.
        self.tab_reset_every_items = int(config.get("tab_reset_every_items", 20))

    def find_title_link(self, li):
        """li 안의 가게명 링크 요소. 없으면 None (광고/구분용 li 등)"""
        links = li.find_elements(By.XPATH, self.TITLE_LINK_XPATH)
        return links[0] if links else None

    def get_item_title(self, li) -> str:
        link = self.find_title_link(li)
        if link is None:
            return ""
        spans = link.find_elements(By.XPATH, './span[1]')
        text = (spans[0].text if spans else link.text).strip()
        # 가게명 span 안에 배지/부가 문구가 줄바꿈으로 더 붙는 경우가 있어 첫 줄만 가게명으로 쓴다
        return text.splitlines()[0].strip() if text else ""

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
            if is_session_dead_error(e):
                raise BrowserSessionDead(str(e)) from e
            if is_renderer_dead_error(e):
                raise BrowserTabDead(str(e).splitlines()[0]) from e
            return False

    # ──────────────────────────────────────────────────────────
    # 복구: 크롬을 다시 띄우지 않고, 같은 브라우저 안에서 프레임만 다시 잡는다
    # ──────────────────────────────────────────────────────────
    def reenter_search_frame(self) -> bool:
        """최상위 프레임으로 나갔다가 searchIframe 으로 다시 들어간다."""
        try:
            self.driver.switch_to.default_content()
        except Exception as e:
            if is_session_dead_error(e):
                raise BrowserSessionDead(str(e)) from e
            self.log(f"[WARN] default_content 복귀 실패: {e}")
        return self.switch_to_frame("searchIframe")

    def ensure_on_page(self, page_number: int, titles: list) -> bool:
        """
        프레임 재진입 후 리스트가 처음 페이지로 되돌아가 있으면(iframe 자체가 새로 로드된 경우)
        원래 보던 페이지 번호로 다시 이동한다. 같은 페이지면 아무것도 하지 않는다.
        """
        if page_number <= 1 or not titles:
            return True
        try:
            first_li = self.driver.find_elements(By.XPATH, '//*[@id="app-root"]//ul[1]/li')
            if not first_li:
                return self.go_to_page(page_number)
            first_title = self.get_item_title(first_li[0])
            if first_title == titles[0]:
                return True
            self.log(f"[복구] 리스트가 다른 페이지를 보여주고 있어 {page_number}p 로 다시 이동합니다.")
            return self.go_to_page(page_number)
        except Exception as e:
            if is_session_dead_error(e):
                raise BrowserSessionDead(str(e)) from e
            self.log(f"[WARN] 현재 페이지 확인 실패, {page_number}p 로 이동 시도: {e}")
            return self.go_to_page(page_number)

    def recover_frame(self, page_number: int, titles: list, reason: str) -> bool:
        """프레임/요소 참조가 깨졌을 때의 복구 루틴. 브라우저는 그대로 둔다."""
        self.log(f"[복구] 프레임 재진입 시도 (원인: {reason})")
        if not self.reenter_search_frame():
            return False
        return self.ensure_on_page(page_number, titles)

    def raise_if_session_dead(self, e: Exception):
        if is_session_dead_error(e):
            raise BrowserSessionDead(str(e)) from e

    @staticmethod
    def _short(e: Exception) -> str:
        return f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"

    # ──────────────────────────────────────────────────────────
    # 탭 교체: 쌓인 메모리를 비우거나(Out of Memory 예방) 죽은 탭을 버리고 이어가기
    # ──────────────────────────────────────────────────────────
    def reset_page(self, page_number: int, reason: str) -> bool:
        """
        새 탭을 열어 처음부터 다시 검색하고 page_number 페이지로 이동한다. 크롬은 그대로 둔다.
        네이버 지도는 항목을 클릭할수록 탭 메모리가 쌓여 수백 개쯤에서 탭이 죽으므로
        페이지가 바뀔 때마다 호출해 메모리를 비운다. 탭이 이미 죽었을 때의 복구에도 쓴다.
        """
        self.log(f"[탭 교체] {reason} → 새 탭에서 '{self.keyword}' 재검색 후 {page_number}p 로 이동")
        if not self.utils.open_fresh_tab(self.driver, self.config.get("main_url")):
            return False
        try:
            SearchKeyword(self.driver, self.config, log=self.log).enter_keyword(self.keyword)
        except Exception as e:
            self.raise_if_session_dead(e)
            self.log(f"[ERROR] 재검색 실패: {self._short(e)}")
            return False
        self.utils.sleep_random("검색 결과 로딩 대기", self.sleep_normal_sec)
        if not self.reenter_search_frame():
            return False
        return self.go_to_page(page_number)

    def click_all_list_items_across_pages(self, keyword) -> bool:
        """
        검색 결과 리스트의 모든 항목을 페이지를 넘겨 가며 클릭하고 정보를 수집한다.
        :return: True(끝까지 처리), False(브라우저 세션이 끊겨 중단)
        """
        self.keyword = keyword
        page_number = 1
        index = 0            # 현재 페이지에서 처리할 항목 위치 (탭 교체 후에도 이어서 진행)
        titles = []
        tab_recover_tries = 0
        last_reset_index = 0  # 이 페이지에서 마지막으로 탭을 갈아탄 항목 위치
        collected_names = set()  # 이 키워드에서 이미 수집한 가게명 (리스트에 광고/추가 항목이 끼어들어 밀려도 중복 수집 방지)

        try:
            # 1. iframe(서치아이프레임) 으로 진입
            if not self.reenter_search_frame():
                return True  # 검색 결과 프레임이 없으면 이 키워드는 건너뜀

            while True:
                try:
                    self.log(f"[페이지 {page_number}] 리스트 항목 처리 시작" + (f" ({index + 1}번째부터)" if index else ""))

                    # 스크롤 타겟: _pcmap_list_scroll_container
                    if not self.scroll_through_list_container():
                        self.log("[오류] 스크롤 중 오류 발생 ")

                    # 3. li 목록 가져오기 (페이지마다 동적으로 다시 로드해야 함)
                    li_elements = self.wait.until(
                        EC.presence_of_all_elements_located((By.XPATH, '//*[@id="app-root"]//ul[1]/li'))
                    )

                    titles = [self.get_item_title(li) for li in li_elements]
                    self.log(f"[INFO]{page_number}p {len(titles)}개의 항목 발견됨.")
                    # 처음 5개 항목만 로그로 출력 (PySide6에서 긴 문자열 출력이 문제를 일으킴)
                    preview_count = 5
                    preview_titles = titles[:preview_count]
                    self.log(f" └▶ 대표 {preview_count}개: {'|'.join(preview_titles)}")

                    recover_tries = 0
                    reset_pending = False

                    while index < len(li_elements):
                        # ✅ 페이지 중간에도 일정 개수마다 새 탭으로 갈아타 메모리를 비운다
                        if index - last_reset_index >= self.tab_reset_every_items:
                            self.log(f"[MEM] {page_number}p {index}개 처리, 탭 메모리: {self.utils.get_memory_metrics(self.driver)}")
                            last_reset_index = index
                            if not self.reset_page(page_number, f"{page_number}p {index}개 처리, 메모리 비우기"):
                                raise BrowserTabDead("새 탭 전환 실패")
                            reset_pending = True
                            break  # 바깥 루프에서 리스트를 다시 읽고 같은 index 부터 이어감

                        # ✅ 리스트 매 10개마다 메모리 정리
                        if index % 10 == 0:
                            self.log(f"[GC] {index}번째 항목 처리 중, 메모리 정리 수행")
                            gc.collect()

                        try:
                            # 다시 가져와야 stale element 방지됨
                            li_elements = self.driver.find_elements(By.XPATH, '//*[@id="app-root"]//ul[1]/li')
                            target_li = li_elements[index]

                            # 내부의 a 태그 (장소명 링크) 클릭
                            link_elem = self.find_title_link(target_li)
                            if link_elem is None:
                                self.log(f"[SKIP] {page_number}p {index + 1}번째 항목에 가게명 링크가 없어 건너뜀")
                                index += 1
                                continue

                            # 가게명은 클릭 시점의 리스트에서 다시 읽는다 (처리 중 리스트가 늘어나거나 밀릴 수 있음)
                            item = self.get_item_title(target_li) or (titles[index] if index < len(titles) else "Unknown")
                            if item in collected_names:
                                self.log(f"[SKIP] {page_number}p {index + 1}번째 항목({item})은 이미 수집됨")
                                index += 1
                                continue

                            self.driver.execute_script("arguments[0].scrollIntoView(true);", link_elem)
                            link_elem.click()
                            self.utils.sleep_random(f"[클릭] {page_number}p {index + 1}번째 항목({item}) 클릭 완료", self.sleep_short_sec)

                            # 상세 정보 가져오기
                            info = self.handle_entry_iframe_and_extract_info()
                            if not info.get('name'):
                                info['name'] = item  # 상세 프레임에서 못 찾으면 리스트의 가게명 사용
                            self.log(f"[INFO] : {info}")

                            # 상세 화면의 가게명으로 한 번 더 중복 확인 (리스트 가게명이 비었거나 다르게 표기된 경우 대비)
                            if info['name'] in collected_names:
                                self.log(f"[SKIP] {page_number}p {index + 1}번째 항목({info['name']})은 이미 수집됨 (상세 확인)")
                                index += 1
                                continue

                            # 엑셀에 저장
                            append_info_row_to_excel(
                                search_keyword=keyword,
                                page=page_number,
                                index=index,
                                info=info,
                                output_path=self.excel_output_path  # → "result_map_20250612152200.xlsx" 형식 등
                            )

                            collected_names.add(info['name'])
                            if item:
                                collected_names.add(item)
                            index += 1  # 성공 시 다음으로 진행
                            recover_tries = 0
                            tab_recover_tries = 0

                        except (BrowserSessionDead, BrowserTabDead):
                            raise
                        except Exception as e:
                            self.raise_if_session_dead(e)
                            if is_renderer_dead_error(e):
                                raise BrowserTabDead(self._short(e)) from e

                            self.log(f"[에러] {page_number}p {index + 1}번째 항목 클릭 중:", e)

                            if is_frame_lost_error(e):
                                # ⚠️ 프레임/요소 참조가 깨진 경우 → 프레임만 다시 잡고 같은 항목 재시도.
                                #    그래도 안 되면 탭을 통째로 갈아끼운다 (바깥 루프).
                                recover_tries += 1
                                if recover_tries > self.MAX_FRAME_RECOVER_TRIES:
                                    raise BrowserTabDead("프레임 복구 반복 실패") from e
                                self.log(f"[알림] 프레임 복구 {recover_tries}/{self.MAX_FRAME_RECOVER_TRIES}")
                                if self.recover_frame(page_number, titles, self._short(e)):
                                    continue  # 🔁 같은 index 부터 다시
                                raise BrowserTabDead("프레임 복구 실패") from e

                            index += 1  # 그 외 에러는 다음 항목으로 넘어감
                            recover_tries = 0

                    if reset_pending:
                        continue  # 페이지 중간 탭 교체 후 같은 페이지를 다시 읽는다

                    # 페이지 처리 끝. 다음 페이지가 있으면 새 탭에서 이어간다 (메모리 비우기)
                    if not self.has_next_page():
                        self.log("[종료] 더 이상 다음 페이지가 없습니다.")
                        return True

                    self.log(f"[MEM] {page_number}p 완료, 탭 메모리: {self.utils.get_memory_metrics(self.driver)}")
                    page_number += 1
                    index = 0
                    last_reset_index = 0
                    tab_recover_tries = 0
                    if not self.reset_page(page_number, f"{page_number - 1}p 완료, 메모리 비우기"):
                        raise BrowserTabDead("새 탭 전환 실패")

                except BrowserSessionDead:
                    raise
                except BrowserTabDead as e:
                    tab_recover_tries += 1
                    self.log(f"[알림] 탭 응답 없음 ({e}) → 탭 복구 {tab_recover_tries}/{self.MAX_TAB_RECOVER_TRIES}")
                    if tab_recover_tries > self.MAX_TAB_RECOVER_TRIES:
                        self.log("[중단] 탭 복구 반복 실패로 이 키워드 처리를 종료합니다.")
                        return True
                    if self.reset_page(page_number, "탭 복구"):
                        continue  # 같은 페이지, 같은 index 부터 이어서
                    # reset 실패 시 다음 바퀴에서 다시 시도 (횟수 제한 있음)
                    continue

        except BrowserSessionDead as e:
            self.log(f"[FATAL] 브라우저 세션이 끊겨 '{keyword}' 처리를 중단합니다: {e}")
            return False
        except Exception as e:
            self.log("[에러] 페이지 처리 중 중단:", e)
            if is_session_dead_error(e):
                return False
            return True

    # ──────────────────────────────────────────────────────────
    # 페이지네이션
    # ──────────────────────────────────────────────────────────
    def get_page_buttons(self) -> dict:
        """보이는 페이지 번호 버튼 {번호: 요소}"""
        buttons = {}
        for a in self.driver.find_elements(By.XPATH, '//a[@role="button" and string-length(normalize-space(text())) > 0]'):
            text = a.text.strip()
            if text.isdigit():
                buttons[int(text)] = a
        return buttons

    def get_current_page_number(self, buttons: dict):
        """
        현재 페이지 번호. 현재 페이지 버튼에만 붙는 추가 클래스(난독화라 이름은 모름)를
        '다른 버튼에는 없는 클래스 토큰' 으로 찾아낸다. 판별 불가면 None.
        """
        if not buttons:
            return None
        if len(buttons) == 1:
            return next(iter(buttons))
        classes = {n: set((a.get_attribute("class") or "").split()) for n, a in buttons.items()}
        common = set.intersection(*classes.values())
        for n, cls in classes.items():
            if cls - common:
                return n
        return None

    def has_next_page(self) -> bool:
        try:
            next_btn = self.driver.find_element(By.XPATH, '(//a[./span[text()="다음페이지"]])[last()]')
            return next_btn.get_attribute("aria-disabled") != "true"
        except Exception as e:
            self.raise_if_session_dead(e)
            if is_renderer_dead_error(e):
                raise BrowserTabDead(self._short(e)) from e
            self.log(f"[WARN] 다음페이지 버튼 확인 실패(없는 것으로 간주): {self._short(e)}")
            return False

    def go_to_page(self, page_number: int) -> bool:
        """
        page_number 페이지로 이동한다. 번호 버튼이 보이면 바로 클릭하고,
        보이지 않으면 '다음페이지'/'이전페이지' 로 번호 창을 옮겨 가며 찾는다.
        :return: True(이동 완료), False(그런 페이지가 없거나 이동 실패)
        """
        try:
            for _ in range(30):
                buttons = self.get_page_buttons()
                if not buttons:
                    # 페이지네이션 자체가 없으면 결과가 한 페이지뿐
                    return page_number == 1
                current = self.get_current_page_number(buttons)
                if current == page_number:
                    break
                if page_number in buttons:
                    buttons[page_number].click()
                    self.utils.sleep_random(f"{page_number}p 버튼 클릭", self.sleep_short_sec)
                    if self.get_current_page_number(self.get_page_buttons()) in (page_number, None):
                        break
                    continue
                # 번호가 보이는 범위 밖 → 다음/이전 페이지 버튼으로 범위를 옮긴다
                label = "다음페이지" if page_number > max(buttons) else "이전페이지"
                nav_btn = self.driver.find_element(By.XPATH, f'(//a[./span[text()="{label}"]])[last()]')
                if nav_btn.get_attribute("aria-disabled") == "true":
                    self.log(f"[WARN] {page_number}p 는 존재하지 않습니다 (마지막 페이지: {max(buttons)})")
                    return False
                nav_btn.click()
                self.utils.sleep_random(f"{label} 클릭", self.sleep_short_sec)
            else:
                self.log(f"[WARN] {page_number}p 로 이동하지 못했습니다.")
                return False

            self.utils.sleep_random(f"{page_number}p 페이지 이동")
            self.scroll_through_list_container()
            return True
        except Exception as e:
            self.raise_if_session_dead(e)
            if is_renderer_dead_error(e):
                raise BrowserTabDead(self._short(e)) from e
            self.log(f"[WARN] 페이지 {page_number} 이동 실패:", e)
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
        tab_dead = False
        try:
            # 1. 메인 프레임으로 복귀
            self.driver.switch_to.default_content()
            #self.utils.sleep_random("상세정보 추출 준비", self.sleep_short_sec)

            # 2. 엔트리아이프레임 으로 진입
            if not self.switch_to_frame("entryIframe"):
                return dict(EMPTY_INFO)


            # iframe 안에서 렌더링된 내용의 구조를 디버깅하거나 특정 DOM 탐지 오류를 추적
            #html = self.driver.page_source
            #start_index = html.find('<div id="app-root">')

            # 3. 정보 추출
            info = self.extract_info_from_place_section()
            # self.log(f"[INFO] 추출된 정보: {info}")

        except (BrowserSessionDead, BrowserTabDead):
            tab_dead = True
            raise
        except Exception as e:
            self.log(f"[ERROR] (상세) 엔트리아이프레임 진입 또는 정보 추출 실패: {e}")
            if is_session_dead_error(e):
                raise BrowserSessionDead(str(e)) from e
            if is_renderer_dead_error(e):
                tab_dead = True
                raise BrowserTabDead(str(e).splitlines()[0]) from e
            info = dict(EMPTY_INFO)

        finally:
            # 탭이 죽었으면 프레임 복귀 명령도 전부 타임아웃만 나므로 건너뛴다 (바깥에서 새 탭으로 복구)
            if not tab_dead:
                # 4. 메인 프레임 복귀
                try:
                    self.driver.switch_to.default_content()
                    #self.utils.sleep_random("리스트로 이동 중", self.sleep_short_sec)
                except Exception as e:
                    self.log(f"[ERROR] 메인 프레임 복귀 실패: {e}")

                # 5. 다시 서치아이프레임 으로 진입
                try:
                    if not self.switch_to_frame("searchIframe"):
                        self.log("[ERROR] (리스트) 서치아이프레임 복귀 실패")
                    #self.log("[INFO] (리스트) 서치아이프레임 복귀 완료")
                except (BrowserSessionDead, BrowserTabDead):
                    raise
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
                    info['name'] = None
                    # 예전 DOM: place_section > div#_title > div > span[1]
                    # 현재 DOM: place_section > div > span (예: <div class="WmLT9"><span class="PbnXW">가게명</span></div>)
                    #   단, 가게명 div 앞에 "플레이스 플러스" 배지 div 가 끼어드는 경우가 있다.
                    #   배지는 svg + 숨김 텍스트(span.place_blind)로만 되어 있으므로
                    #   "직접 텍스트를 가진 span 중 place_blind 가 아닌 첫 번째" 를 가게명으로 본다.
                    for name_xpath in (
                        './/div[@id="_title"]//div/span[1]',
                        '(./div//span[normalize-space(text()) and not(contains(@class, "place_blind"))])[1]',
                    ):
                        name_elems = place_sections[0].find_elements(By.XPATH, name_xpath)
                        if not name_elems:
                            continue
                        name = name_elems[0].text.strip()
                        if name and name not in self.NAME_BADGE_TEXTS:
                            info['name'] = name
                            break
                    if not info['name']:
                        self.log("[WARN] 상세 프레임에서 이름을 찾지 못함 (리스트의 가게명으로 대체)")
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
                    # 현재 DOM 에는 예전 공유 레이어(spi_copyurl)가 없다.
                    # 상세 프레임 링크(/place/{id}/...)에서 place id 를 뽑아 장소 URL 을 만든다.
                    info['share'] = None
                    try:
                        # 링크 형식은 업종별로 /place/ID, /restaurant/ID, /cafe/ID ... 로 다르지만
                        # 장소 URL 은 공통으로 /p/entry/place/ID 를 쓸 수 있다.
                        place_links = self.driver.find_elements(
                            By.XPATH, '//a[contains(@href, "/photo") or contains(@href, "/review") or contains(@href, "/home")]'
                        )
                        for a in place_links[:5]:
                            m = re.search(r"/[a-z]+/(\d{6,})(?:[/?#]|$)", a.get_attribute("href") or "")
                            if m:
                                info['share'] = f"https://map.naver.com/p/entry/place/{m.group(1)}"
                                break
                    except Exception:
                        pass
                    if not info['share']:
                        self.log(f"[WARN] 공유 추출 실패: {e}")

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
        return dict(EMPTY_INFO)


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