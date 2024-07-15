#!/usr/bin/python3

from os import path, mkdir, getcwd, chdir, getenv
from sys import exit, stdout, stderr
from typing import Dict, List, Optional
from requests import get, post
from platform import system
from hashlib import sha256
from shutil import move
from time import perf_counter, sleep

from PyQt6.QtWidgets import (QApplication, QVBoxLayout, QHBoxLayout, QWidget,
                             QPushButton, QLineEdit, QLabel, QProgressBar, QScrollArea, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot

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
    finished_signal = pyqtSignal()

    def __init__(self, url: str, password: Optional[str], max_workers: int) -> None:
        super().__init__()
        self.url = url
        self.password = password
        self.max_workers = max_workers

    def run(self) -> None:
        main = Main(self.url, self.password, self.max_workers, self.progress_signal, self.message_signal)
        main.start_downloads()
        self.finished_signal.emit()

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
        self._sequentialDownloads()

    def _sequentialDownloads(self) -> None:
        chdir(self._root_dir)

        for item in self._files_link_list:
            self._downloadContent(item, self._token, 16384)
            sleep(2)  # Espera 2 segundos entre descargas

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
                self.progress_signal.emit(100)
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
        self.download_threads = []

    def initUI(self) -> None:
        self.setWindowTitle('Gofile Downloader')
        self.setGeometry(100, 100, 600, 600)

        self.layout = QVBoxLayout()

        self.downloads_container = QVBoxLayout()
        self.add_download_ui()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_content.setLayout(self.downloads_container)
        self.scroll_area.setWidget(self.scroll_content)

        self.add_download_button = QPushButton('New download')
        self.add_download_button.clicked.connect(self.add_download_ui)

        self.download_button = QPushButton('Download')
        self.download_button.clicked.connect(self.start_all_downloads)

        self.quit_button = QPushButton('Quit')
        self.quit_button.clicked.connect(self.check_active_downloads_before_exit)

        self.layout.addWidget(self.scroll_area)
        self.layout.addWidget(self.add_download_button)
        self.layout.addWidget(self.download_button)
        self.layout.addWidget(self.quit_button)
        self.setLayout(self.layout)

    def add_download_ui(self) -> None:
        download_ui = DownloadUI(self)
        self.downloads_container.addWidget(download_ui)

    def start_all_downloads(self) -> None:
        self.current_download_index = 0
        self.start_next_download()

    def start_next_download(self) -> None:
        if self.current_download_index < self.downloads_container.count():
            download_ui = self.downloads_container.itemAt(self.current_download_index).widget()
            url = download_ui.url_input.text()
            password = download_ui.password_input.text()
            self.start_download(url, password, download_ui.progress_bar, download_ui.message_label)
        else:
            print("All downloads completed.")

    def start_download(self, url: str, password: str, progress_bar: QProgressBar, message_label: QLabel) -> None:
        download_thread = DownloadThread(url, password, max_workers=1)
        self.download_threads.append(download_thread)  # Almacenar referencia al hilo
        download_thread.progress_signal.connect(progress_bar.setValue)
        download_thread.message_signal.connect(message_label.setText)
        download_thread.finished_signal.connect(self.on_download_finished)
        download_thread.start()

    @pyqtSlot()
    def on_download_finished(self) -> None:
        self.current_download_index += 1
        self.start_next_download()

    def check_active_downloads_before_exit(self) -> None:
        if any(thread.isRunning() for thread in self.download_threads):
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setText("There are active downloads. Are you sure you want to exit?")
            msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            result = msg_box.exec()

            if result == QMessageBox.StandardButton.Yes:
                self.close()
        else:
            self.close()

    def closeEvent(self, event) -> None:
        for thread in self.download_threads:
            thread.quit()
            thread.wait()
        super().closeEvent(event)

class DownloadUI(QWidget):
    def __init__(self, parent: DownloadApp) -> None:
        super().__init__()
        self.parent = parent
        self.initUI()

    def initUI(self) -> None:
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

        self.message_label = QLabel('')

        layout.addLayout(url_layout)
        layout.addLayout(password_layout)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.message_label)

        self.setLayout(layout)

if __name__ == '__main__':
    import sys
    app = QApplication(sys.argv)
    ex = DownloadApp()
    ex.show()
    sys.exit(app.exec())
