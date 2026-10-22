```
이미 세팅하였다면 이번 프로젝트에서는 안 해도 됨.
0. 파이썬 셀레니움(Selenium) 세팅
(1) Python 3.12.10 (Windows (64-bit))
python-3.12.10-amd64.zip  
https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.zip (30.9M)
zip 다운로드 받은 후 압축 풀기

환경변수 PYTHON_HOME 에 D:/dev/Python312 (backslash)

%PYTHON_HOME% 를 환경변수 PATH 에 추가
%PYTHON_HOME%\Scripts (backslash)를 환경변수 PATH 에 추가

(2) Python에 pip 설치 (pip 가 없을 때만)
cmd 관리자 권한으로 실행
d:
cd D:/dev/Python312
pip --version
# 위 명령이 실패할 때만 아래 두 줄 실행
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
python get-pip.py

#(임베디드 버전이었을 때) python312._pth 파일 수정
#(임베디드 버전이었을 때) python312._pth 파일의 마지막에 아래를 추가.
#(임베디드 버전이었을 때) Lib/site-packages

(3) uv 설치
cmd 관리자 권한으로 실행
d:
cd D:\dev\Python312\Scripts
pip install uv
uv --version
#uv venv .venv
#uv venv -p 3.12
#uv pip install PySide6

python -c "from PySide6.QtWidgets import QApplication, QLabel; print('PySide6 OK')"
# PySide6 동작 확인은 1번 단계에서 가상환경에 설치한 뒤 uv run 으로 한다.
```

```
1. 현재 프로젝트 세팅
(1) uv 로 pyinstaller, pyside6, selenium, webdriver-manager 설치
## 윈도우 uv 사용법 > bigadmin.tistory.com/entry/Python-패키지-관리의-혁신-uv-소개
cmd 관리자 권한으로 실행
d:
cd D:/dev/workspaces/map_naver_search
#uv venv .venv
uv venv -p 3.12
uv pip install pyinstaller pyside6 selenium webdriver-manager
uv pip install pandas openpyxl beautifulsoup4
# 아래 두 줄은 지금은 안 쓰인다. 옛 복구 코드(크롬 프로세스 강제 종료 후 재실행)에서 쓰였다.
#uv pip install pygetwindow pyautogui pywin32
#uv pip install psutil
uv run pyinstaller --onefile --windowed hello_gui.py
uv run -- python -c "from PySide6.QtWidgets import QApplication, QLabel; print('PySide6 OK')"
uv run test_pyside6.py

(참고) D:/dev/workspaces/map_naver_search/hello_gui.py
from PySide6.QtWidgets import QApplication, QLabel
import sys

app = QApplication(sys.argv)
label = QLabel("Hello from PySide6!")
label.show()
app.exec()

(참고) test_pyside6.py
from PySide6.QtWidgets import QApplication, QLabel
print("PySide6 OK")
```

```
2. 개발툴 pycharm 세팅
인터프리터
## google > pycharm venv 설정
## > uiandwe.tistory.com/1215
(만약 pycharm 으로 New Project 로 생성하였다면 이미 생성되어 있음.)
#File > Settings > Project > Python Interpreter > Show all > add(+) 
#> D:/dev/workspaces/map_naver_search/.venv/Scripts/python.exe
#> OK > OK
```

```
3. 실행파일 만들기
cmd 관리자 권한으로 실행
d:
cd D:/dev/workspaces/map_naver_search
del main.spec
uv run pyinstaller main.py --onefile --windowed --name main
#uv run pyinstaller main.py --onefile --windowed --name main --paths search_logic --paths results --paths file_logic --paths common_logic
```

```
4. 구조
map_naver_search/
├── main.py              ← 메인 코드
├── key.ini              ← 외부 설정파일 (tab_reset_every_items: 항목 몇 개마다 새 탭으로 갈아탈지, 기본 20)
├── list.txt             ← 검색 키워드 목록 (한 줄에 하나, 없으면 실행 중단)
├── chromedriver.exe     ← (선택) 인터넷이 막힌 PC용. exe 옆에 두면 다운로드 없이 이 파일을 사용
├── common_logic/
│   ├── __init__.py
│   └── utils.py
├── search_logic/
│   ├── __init__.py   ← 해당 디렉터리를 포함하려면 반드시 필요
│   └── keyword.py
├── results/
│   ├── __init__.py
│   └── paged_list_scraper.py
└── file_logic/
    ├── __init__.py
    └── excel.py

```

```
5. 모든 chromedriver 프로세스 종료
프로그램 창을 닫으면 크롬과 크롬드라이버도 같이 종료되므로 평소에는 필요 없다.
비정상 종료 뒤 chromedriver.exe 가 남아 있을 때만 실행.
cmd 관리자 권한으로 실행
taskkill /f /im chromedriver.exe