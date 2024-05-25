#!/usr/bin/python3

from os import path, mkdir, getcwd, chdir, getenv
from sys import exit, stdout, stderr
from typing import Dict, List, Optional
from requests import get, post
from concurrent.futures import ThreadPoolExecutor
from platform import system
from hashlib import sha256
from shutil import move
from time import perf_counter

from PyQt6.QtWidgets import (QApplication, QVBoxLayout, QHBoxLayout, QWidget, 
                             QPushButton, QLineEdit, QLabel, QProgressBar)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

NEW_LINE: str = "\n" if system() != "Windows" else "\r\n"


def die(_str: str) -> None:
    stderr.write(_str + NEW_LINE)
    stderr.flush()
    exit(-1)


def _print(_str: str) -> None:
    stdout.write(_str)
    stdout.flush()


class DownloadThread(QThread):
    progress_signal = pyqtSignal(int)
    message_signal = pyqtSignal(str)

    def __init__(self, url: str, password: Optional[str], max_workers: int) -> None:
        super().__init__()
        self.url = url
        self.password = password
        self.max_workers = max_workers

    def run(self) -> None:
        main = Main(self.url, self.password, self.max_workers, self.progress_signal, self.message_signal)
        main.start_downloads()


class Main:
    def __init__(self, url: str, password: Optional[str], max_workers: int,
                 progress_signal: pyqtSignal, message_signal: pyqtSignal) -> None:
        try:
            if not url.split("/")[-2] == "d":
                die(f"The url probably doesn't have an id in it: {url}")

            self._id: str = url.split("/")[-1]
        except IndexError:
            die(f"Something is wrong with the url: {url}.")

        self._downloaddir: Optional[str] = getenv("GF_DOWNLOADDIR")

        if self._downloaddir and path.exists(self._downloaddir):
            chdir(self._downloaddir)

        self._root_dir: str = path.join(getcwd(), self._id)
        self._token: str = self._getToken()
        self._password: Optional[str] = sha256(password.encode()).hexdigest() if password else None
        self._max_workers: int = max_workers

        self._files_link_list: List[Dict] = []
        self.progress_signal = progress_signal
        self.message_signal = message_signal

        self._createDir(self._id)
        chdir(self._id)
        self._parseLinks(self._id, self._token, self._password)

    def start_downloads(self) -> None:
        self._threadedDownloads()

    def _threadedDownloads(self) -> None:
        chdir(self._root_dir)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for item in self._files_link_list:
                executor.submit(self._downloadContent, item, self._token, 16384)

    def _createDir(self, dirname: str) -> None:
        current_dir: str = getcwd()
        filepath: str = path.join(current_dir, dirname)

        try:
            mkdir(filepath)
        except FileExistsError:
            pass

    @staticmethod
    def _getToken() -> str:
        headers: Dict = {
            "User-Agent": getenv("GF_USERAGENT") if getenv("GF_USERAGENT") else "Mozilla/5.0",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
        }

        create_account_response: Dict = post("https://api.gofile.io/accounts", headers=headers).json()

        if create_account_response["status"] != "ok":
            die("Account creation failed!")

        return create_account_response["data"]["token"]

    def _downloadContent(self, file_info: Dict, token: str, chunk_size: int = 4096) -> None:
        if path.exists(file_info["path"]):
            if path.getsize(file_info["path"]) > 0:
                self.message_signal.emit(f"{file_info['filename']} already exists, skipping.")
                return

        filename: str = file_info["path"] + '.part'
        url: str = file_info["link"]

        headers: Dict = {
            "Cookie": "accountToken=" + token,
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": getenv("GF_USERAGENT") if getenv("GF_USERAGENT") else "Mozilla/5.0",
            "Accept": "*/*",
            "Referer": url + ("/" if not url.endswith("/") else ""),
            "Origin": url,
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache"
        }

        part_size: int = 0
        if path.isfile(filename):
            part_size = int(path.getsize(filename))
            headers["Range"] = f"bytes={part_size}-"

        has_size: Optional[str] = None
        message: str = " "

        try:
            with get(url, headers=headers, stream=True, timeout=(9, 27)) as response_handler:
                if ((response_handler.status_code in (403, 404, 405, 500)) or
                    (part_size == 0 and response_handler.status_code != 200) or
                    (part_size > 0 and response_handler.status_code != 206)):
                    self.message_signal.emit(
                        f"Couldn't download the file from {url}."
                        + NEW_LINE
                        + f"Status code: {response_handler.status_code}"
                        + NEW_LINE
                    )
                    return

                has_size = response_handler.headers.get('Content-Length') \
                    if part_size == 0 \
                    else response_handler.headers.get('Content-Range').split("/")[-1]

                if not has_size:
                    self.message_signal.emit(
                        f"Couldn't find the file size from {url}."
                        + NEW_LINE
                        + f"Status code: {response_handler.status_code}"
                        + NEW_LINE
                    )
                    return

                with open(filename, 'ab') as handler:
                    total_size: float = float(has_size)

                    start_time: float = perf_counter()
                    for i, chunk in enumerate(response_handler.iter_content(chunk_size=chunk_size)):
                        progress: float = (part_size + (i * len(chunk))) / total_size * 100
                        handler.write(chunk)

                        rate: float = (i * len(chunk)) / (perf_counter()-start_time)
                        unit: str = "B/s"
                        if rate < (1024):
                            unit = "B/s"
                        elif rate < (1024*1024):
                            rate /= 1024
                            unit = "KB/s"
                        elif rate < (1024*1024*1024):
                            rate /= (1024 * 1024)
                            unit = "MB/s"
                        elif rate < (1024*1024*1024*1024):
                            rate /= (1024 * 1024 * 1024)
                            unit = "GB/s"

                        self.progress_signal.emit(int(progress))
                        self.message_signal.emit(f"\rDownloading {file_info['filename']}: {round(progress, 1)}% at {round(rate, 1)} {unit}")

        finally:
            if path.getsize(filename) == int(has_size):
                move(filename, file_info["path"])
                self.message_signal.emit(f"\rDownloading {file_info['filename']}: Done!" + NEW_LINE)

    def _cacheLink(self, filepath: str, filename: str, link: str) -> None:
        self._files_link_list.append(
            {
                "path": path.join(filepath, filename),
                "filename": filename,
                "link": link
            }
        )

    def _parseLinks(self, _id: str, token: str, password: Optional[str] = None) -> None:
        url: str = f"https://api.gofile.io/contents/{_id}?wt=4fd6sg89d7s6&cache=true"

        if password:
            url = url + f"&password={password}"

        headers: Dict = {
            "User-Agent": getenv("GF_USERAGENT") if getenv("GF_USERAGENT") else "Mozilla/5.0",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Authorization": "Bearer" + " " + token,
        }

        response: Dict = get(url, headers=headers).json()

        if response["status"] != "ok":
            die(f"Failed to get a link as response from the {url}")

        data: Dict = response["data"]

        if data["type"] == "folder":
            children_ids: List[str] = data["childrenIds"]

            self._createDir(data["name"])
            chdir(data["name"])

            for child_id in children_ids:
                child: Dict = data["children"][child_id]

                if data["children"][child_id]["type"] == "folder":
                    self._parseLinks(child["code"], token, password)
                else:
                    self._cacheLink(getcwd(), child["name"], child["link"])

            chdir(path.pardir)
        else:
            self._cacheLink(getcwd(), data["name"], data["link"])


class DownloadApp(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.initUI()

    def initUI(self) -> None:
        self.setWindowTitle('Gofile Downloader')
        self.setGeometry(100, 100, 600, 200)

        layout = QVBoxLayout()

        url_layout = QHBoxLayout()
        url_label = QLabel('URL:')
        self.url_input = QLineEdit()
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)

        password_layout = QHBoxLayout()
        password_label = QLabel('Password:')
        self.password_input = QLineEdit()
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.password_input)

        self.progress_bar = QProgressBar()
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button_layout = QHBoxLayout()
        self.download_button = QPushButton('Download')
        self.download_button.clicked.connect(self.start_download)
        self.quit_button = QPushButton('Quit')
        self.quit_button.clicked.connect(self.close)
        button_layout.addWidget(self.download_button)
        button_layout.addWidget(self.quit_button)

        self.message_label = QLabel('')

        layout.addLayout(url_layout)
        layout.addLayout(password_layout)
        layout.addWidget(self.progress_bar)
        layout.addLayout(button_layout)
        layout.addWidget(self.message_label)

        self.setLayout(layout)

    def start_download(self) -> None:
        url = self.url_input.text()
        password = self.password_input.text()

        self.download_thread = DownloadThread(url, password, max_workers=3)
        self.download_thread.progress_signal.connect(self.update_progress)
        self.download_thread.message_signal.connect(self.update_message)
        self.download_thread.start()

    def update_progress(self, progress: int) -> None:
        self.progress_bar.setValue(progress)

    def update_message(self, message: str) -> None:
        self.message_label.setText(message)


if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    ex = DownloadApp()
    ex.show()
    sys.exit(app.exec())

