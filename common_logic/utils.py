import glob
import os
import re
import subprocess
import sys
import time
import random

# selenium 4.5x 의 `webdriver.Chrome` / `webdriver.ChromeOptions` 는 지연 임포트(importlib)라
# PyInstaller 가 추적하지 못해 exe 에서 ModuleNotFoundError 가 난다. 반드시 명시적으로 임포트한다.
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# ──────────────────────────────────────────────────────────────
# 예외 분류: 세션(브라우저)이 완전히 죽은 경우 vs 프레임만 어긋난 경우
# ──────────────────────────────────────────────────────────────
# 브라우저/드라이버 프로세스 자체가 사라졌을 때만 나오는 메시지.
# 이 경우에는 어떤 명령도 통하지 않으므로 작업을 중단해야 한다.
_SESSION_DEAD_MARKERS = (
    "invalid session id",
    "session deleted",
    "chrome not reachable",
    "no such window",
    "web view not found",
    "browser has closed",
    "Max retries exceeded",      # chromedriver 포트에 접속 자체가 안 됨
    "Connection refused",
    "ConnectionResetError",
    "RemoteDisconnected",
)

# iframe 교체·DOM 재렌더링 등으로 "현재 프레임/요소 참조"만 깨진 경우.
# 브라우저는 멀쩡하므로 default_content → iframe 재진입만 하면 계속 진행 가능하다.
_FRAME_LOST_MARKERS = (
    "target frame detached",
    "no such frame",
    "stale element reference",
    "element is not attached",
    "cannot determine loading status",
    "disconnected: not connected to DevTools",
    "Read timed out",
)

# 탭(렌더러)이 죽은 경우. 네이버 지도는 항목을 수백 개 클릭하면 메모리가 쌓여 탭이
# "앗, 이런! Out of Memory" 로 죽는데, 그러면 모든 명령이 렌더러 타임아웃으로 실패한다.
# 브라우저 자체는 살아 있으므로 새 탭을 열고 죽은 탭을 닫으면 계속 진행할 수 있다.
_RENDERER_DEAD_MARKERS = (
    "Timed out receiving message from renderer",
    "tab crashed",
    "page crash",
    "Out of Memory",
    "target crashed",
)


def is_session_dead_error(e: Exception) -> bool:
    msg = f"{type(e).__name__}: {e}"
    return any(marker in msg for marker in _SESSION_DEAD_MARKERS)


def is_frame_lost_error(e: Exception) -> bool:
    msg = f"{type(e).__name__}: {e}"
    return any(marker in msg for marker in _FRAME_LOST_MARKERS)


def is_renderer_dead_error(e: Exception) -> bool:
    msg = f"{type(e).__name__}: {e}"
    return any(marker in msg for marker in _RENDERER_DEAD_MARKERS)


def get_base_path() -> str:
    """exe 로 실행 중이면 exe 가 있는 폴더, 아니면 프로젝트 루트"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_VERSION_RE = re.compile(r"\d+\.\d+\.\d+\.\d+")


def _version_tuple(v: str):
    return tuple(int(x) for x in v.split("."))


def detect_chrome_version() -> str | None:
    """설치된 크롬 버전 (예: '155.0.8059.85'). 못 찾으면 None"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon") as k:
            v, _ = winreg.QueryValueEx(k, "version")
            if _VERSION_RE.fullmatch(v):
                return v
    except Exception:
        pass
    candidates = []
    for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                 os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        app_dir = os.path.join(base, "Google", "Chrome", "Application")
        if os.path.isdir(app_dir):
            for name in os.listdir(app_dir):
                if _VERSION_RE.fullmatch(name):
                    candidates.append(name)
    return max(candidates, key=_version_tuple) if candidates else None


def find_cached_chromedrivers() -> list[tuple[str, str]]:
    """webdriver-manager 가 예전에 받아둔 드라이버 목록 [(version, path)], 최신 버전 순"""
    root = os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "chromedriver")
    found = []
    for path in glob.glob(os.path.join(root, "*", "*", "**", "chromedriver.exe"), recursive=True):
        m = _VERSION_RE.search(path.replace(root, ""))
        if m:
            found.append((m.group(0), path))
    found.sort(key=lambda t: _version_tuple(t[0]), reverse=True)
    return found


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

    def create_chrome_driver(self) -> ChromeWebDriver:
        """
        Automatically download and run Chrome driver.

        윈도우 11 에서 몇 분 지나면 드라이버 응답이 끊기던 원인은
        크롬이 "백그라운드/가려진 창"을 절전 대상으로 보고 렌더러·타이머를 멈추기 때문이다
        (창을 최소화하거나 다른 창에 가려지면 발생). 아래 플래그로 이를 막는다.
        """
        options = ChromeOptions()
        # options.add_argument("--start-maximized")
        # options.add_argument("--headless")

        # ── 윈도우 절전/쓰로틀링 방지 (드라이버 유지의 핵심) ──
        options.add_argument("--disable-background-timer-throttling")   # 백그라운드 탭 타이머 지연 금지
        options.add_argument("--disable-backgrounding-occluded-windows") # 다른 창에 가려져도 렌더링 유지
        options.add_argument("--disable-renderer-backgrounding")         # 렌더러 우선순위 강등 금지
        options.add_argument("--disable-hang-monitor")                   # "응답 없음" 다이얼로그 금지
        # CalculateNativeWinOcclusion: 윈도우 전용, 창이 가려졌다고 판단되면 렌더링을 멈추는 기능
        # HighEfficiencyModeAvailable / BatterySaverModeAvailable: 메모리 절약 모드·절전 모드
        options.add_argument(
            "--disable-features=CalculateNativeWinOcclusion,"
            "HighEfficiencyModeAvailable,BatterySaverModeAvailable,"
            "Translate,OptimizationHints"
        )

        # ── 기타 안정화 ──
        # --disable-gpu 는 쓰지 않는다. 지도 캔버스를 소프트웨어 렌더링으로 그리게 되어
        # 렌더러 메모리를 더 쓰고(Out of Memory 를 앞당김) CPU 도 더 먹는다.
        options.add_argument("--disable-extensions")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-session-crashed-bubble")
        options.add_argument("--disable-popup-blocking")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])

        # 페이지 로딩을 DOMContentLoaded 까지만 기다린다.
        # 네이버 지도는 지도 타일/롱폴링 때문에 load 이벤트가 늦게 오거나 안 와서
        # click()/get() 이 수 분간 블로킹되어 "Read timed out" 이 났었다.
        options.page_load_strategy = "eager"

        driver = self._launch_with_fallbacks(options)

        # 명령 하나가 무한정 걸리지 않도록 상한을 둔다 (초 단위).
        # 탭이 죽었을 때 명령마다 이 시간만큼 기다리므로 너무 길게 잡지 않는다.
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(20)
        self.enable_memory_metrics(driver)

        self.log(f"[INFO] Chrome 실행 완료 (chrome {driver.capabilities.get('browserVersion')}, "
                 f"chromedriver {driver.capabilities.get('chrome', {}).get('chromedriverVersion', '').split(' ')[0]})")
        return driver

    # ──────────────────────────────────────────────────────────
    # chromedriver 찾기: 인터넷이 막혀 있어도 돌 수 있도록 여러 경로를 차례로 시도
    # ──────────────────────────────────────────────────────────
    def _make_service(self, driver_path: str) -> Service:
        # chromedriver 의 stdout/stderr 를 DEVNULL 로 명시.
        # (PyInstaller --windowed 로 띄우면 콘솔이 없어서 파이프가 막힐 수 있음)
        return Service(driver_path, log_output=subprocess.DEVNULL)

    def _launch_with_fallbacks(self, options: ChromeOptions) -> ChromeWebDriver:
        chrome_version = detect_chrome_version()
        chrome_major = chrome_version.split(".")[0] if chrome_version else None
        self.log(f"[INFO] 설치된 Chrome 버전: {chrome_version or '알 수 없음'}")

        errors = []

        def attempt(name, fn):
            try:
                self.log(f"[INFO] 드라이버 시도: {name}")
                driver = fn()
                self.log(f"[INFO] 드라이버 성공: {name}")
                return driver
            except Exception as e:
                first_line = str(e).splitlines()[0] if str(e) else type(e).__name__
                self.log(f"[WARN] 드라이버 실패 ({name}): {first_line}")
                errors.append(f"{name}: {first_line}")
                return None

        # 1) 실행 파일(또는 프로젝트) 옆에 직접 넣어둔 chromedriver.exe  → 인터넷 없이 동작
        local_path = os.path.join(get_base_path(), "chromedriver.exe")
        if os.path.exists(local_path):
            d = attempt(f"로컬 chromedriver.exe ({local_path})",
                        lambda: ChromeWebDriver(service=self._make_service(local_path), options=options))
            if d:
                return d

        # 2) webdriver-manager 로 다운로드 (인터넷 필요)
        d = attempt("webdriver-manager 다운로드",
                    lambda: ChromeWebDriver(service=self._make_service(ChromeDriverManager().install()), options=options))
        if d:
            return d

        # 3) 예전에 받아둔 캐시 (같은 메이저 버전 우선, 없으면 최신)
        cached = find_cached_chromedrivers()
        same_major = [c for c in cached if chrome_major and c[0].split(".")[0] == chrome_major]
        for version, path in (same_major or cached[:1]):
            d = attempt(f"캐시된 chromedriver {version}",
                        lambda p=path: ChromeWebDriver(service=self._make_service(p), options=options))
            if d:
                return d

        # 4) Selenium 내장 드라이버 관리자 (selenium-manager, 자체 캐시 사용)
        d = attempt("Selenium Manager", lambda: ChromeWebDriver(options=options))
        if d:
            return d

        hint = (f"해결: https://googlechromelabs.github.io/chrome-for-testing/ 에서 "
                f"Chrome {chrome_version or '설치 버전'} 에 맞는 chromedriver(win64)를 받아 압축을 풀고 "
                f"chromedriver.exe 를 {get_base_path()} 에 넣어 주세요.")
        detail = " / ".join(errors)
        raise RuntimeError(f"chromedriver 를 준비하지 못했습니다. ({detail}) {hint}")

    # ──────────────────────────────────────────────────────────
    # 탭 메모리 관리
    # ──────────────────────────────────────────────────────────
    def enable_memory_metrics(self, driver):
        """렌더러 메모리 지표(Performance.getMetrics) 수집을 켠다. 실패해도 무시."""
        try:
            driver.execute_cdp_cmd("Performance.enable", {})
        except Exception:
            pass

    def get_memory_metrics(self, driver) -> str:
        """현재 탭의 문서 수 / DOM 노드 수 / JS 힙 크기를 한 줄 문자열로"""
        try:
            metrics = driver.execute_cdp_cmd("Performance.getMetrics", {}).get("metrics", [])
            m = {x["name"]: x["value"] for x in metrics}
            return (f"documents={int(m.get('Documents', 0))} nodes={int(m.get('Nodes', 0))} "
                    f"jsHeap={m.get('JSHeapUsedSize', 0) / 1e6:.0f}MB")
        except Exception as e:
            return f"(측정 실패: {type(e).__name__})"

    def open_fresh_tab(self, driver, url: str) -> bool:
        """
        새 탭을 열어 url 을 띄우고, 기존 탭은 모두 닫는다.
        - 쌓인 메모리를 비우는 용도 (페이지 바뀔 때마다 호출)
        - 탭이 Out of Memory 로 죽었을 때의 복구 용도
        새 탭 열기/닫기는 렌더러가 아니라 브라우저 수준 명령이라 죽은 탭이 있어도 동작한다.
        """
        try:
            old_handles = list(driver.window_handles)
        except Exception as e:
            self.log(f"[WARN] 창 목록 조회 실패: {type(e).__name__}")
            old_handles = []
        try:
            driver.switch_to.new_window("tab")
            new_handle = driver.current_window_handle
        except Exception as e:
            self.log(f"[ERROR] 새 탭 열기 실패: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
            return False
        for h in old_handles:
            if h == new_handle:
                continue
            try:
                driver.switch_to.window(h)
                driver.close()
            except Exception as e:
                self.log(f"[WARN] 이전 탭 닫기 실패(무시): {type(e).__name__}")
        try:
            driver.switch_to.window(new_handle)
            driver.get(url)
            self.enable_memory_metrics(driver)
            return True
        except Exception as e:
            self.log(f"[ERROR] 새 탭에서 페이지 열기 실패: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
            return False

    def is_driver_alive(self, driver) -> bool:
        """브라우저 세션이 살아 있는지 가벼운 명령으로 확인"""
        if driver is None:
            return False
        try:
            _ = driver.title
            return True
        except Exception as e:
            self.log(f"[WARN] 드라이버 응답 없음: {type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}")
            return False

    def quit_driver(self, driver):
        """앱 종료 시 크롬/크롬드라이버를 깔끔하게 닫는다"""
        if driver is None:
            return
        try:
            driver.quit()
        except Exception:
            pass
