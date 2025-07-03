```
이미 세팅하였다면 이번 프로젝트에서는 안 해도 됨.
0. 파이썬 셀레니움(Selenium) 세팅
(1) Python 3.12 (Windows embeddable package (64-bit))
zip 다운로드 받은 후 압축 풀기
D:/dev/python-3.12.10-embed-amd64 (backslash)를환경변수 PATH 에 해당 경로 추가
D:/dev/python-3.12.10-embed-amd64/Scripts (backslash)를 환경변수 PATH 에 추가

(2) embeddable Python에 pip 설치
cmd 관리자 권한으로 실행
d:
cd D:/dev/python-3.12.10-embed-amd64
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
python get-pip.py

python312._pth 파일 수정
python312._pth 파일의 마지막에 아래를 추가.
Lib/site-packages

github.com/astral-sh/uv/releases 에서 uv-x86_64-pc-windows-msvc.zip 다운로드하고,
압축 풀고 uv.exe를 Python 폴더에 넣기

cmd 관리자 권한으로 실행
d:
cd D:/dev/python-3.12.10-embed-amd64
pip --version
pip install uv
uv --version
uv venv .venv
uv venv -p 3.12
uv pip install PySide6

python -c "from PySide6.QtWidgets import QApplication, QLabel; print('PySide6 OK')"
```

```
1. 현재 프로젝트 세팅
(1) uv 로 pyinstaller, pyside6, selenium, webdriver-manager 설치
## 윈도우 uv 사용법 > bigadmin.tistory.com/entry/Python-패키지-관리의-혁신-uv-소개
cmd 관리자 권한으로 실행
d:
cd D:/dev/workspaces/map_naver_search
uv venv .venv
uv venv -p 3.12
uv pip install pyinstaller pyside6 selenium webdriver-manager
uv pip install pandas openpyxl beautifulsoup4
uv pip install pygetwindow pyautogui pywin32
uv pip install psutil
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
File > Settings > Project > Python Interpreter > Show all > add(+) 
> D:/dev/python-3.12.10-embed-amd64/python.exe
> OK > OK
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
├── key.ini              ← 외부 설정파일
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
cmd 관리자 권한으로 실행
taskkill /f /im chromedriver.exe