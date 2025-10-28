"""
download.py

This module downloads files from specified URLs and saves them locally.
"""

import os
import platform
import tarfile
import zipfile
import requests
from tqdm import tqdm

versions = [
    "0.20.0-beta.rc1",
]  # Replace with your versions

DOWNLOAD_DIR = "download/lnd"
SYSTEMS = {
    "Linux": {
        "x86_64": {
            "url": "https://github.com/lightningnetwork/lnd/releases/download/v{version}/lnd-linux-amd64-v{version}.tar.gz",
            "ext": ".tar.gz",
        },
    },
    "Darwin": {
        "x86_64": {
            "url": "https://github.com/lightningnetwork/lnd/releases/download/v{version}/lnd-darwin-amd64-v{version}.tar.gz",
            "ext": ".tar.gz",
        },
        "arm64": {
            "url": "https://github.com/lightningnetwork/lnd/releases/download/v{version}/lnd-darwin-amd64-v{version}.tar.gz",
            "ext": ".tar.gz",
        },
    },
}


def download_file(url, filename):
    """
    Download a file from the specified URL and save it locally.

    Args:
        url (str): The URL of the file to download.
        filename (str): The name to save the downloaded file as.

    Raises:
        requests.HTTPError: If an HTTP error occurs during the download.

    """
    print(f"Downloading URL: {url}")
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()

    total_size = int(response.headers.get("content-length", 0))
    block_size = 1024  # 1 Kibibyte
    tq_file = tqdm(total=total_size, unit="iB", unit_scale=True)

    with open(filename, "wb") as file:
        for data in response.iter_content(block_size):
            tq_file.update(len(data))
            file.write(data)
    tq_file.close()

    if total_size not in (0, total_size):
        raise requests.HTTPError("ERROR: Something went wrong during the download.")


def extract_file(filename, path):
    """
    Extract a compressed file to the specified path.

    Args:
        filename (str): The name of the compressed file.
        path (str): The path to extract the files to.
    """
    temp_path = path
    os.makedirs(temp_path, exist_ok=True)

    if filename.endswith(".zip"):
        with zipfile.ZipFile(filename, "r") as zip_ref:
            zip_ref.extractall(temp_path)
    elif filename.endswith(".tar.gz"):
        with tarfile.open(filename, "r:gz") as tar_ref:
            tar_ref.extractall(temp_path)
            extracted_items = os.listdir(temp_path)
            if len(extracted_items) == 1 and os.path.isdir(
                os.path.join(temp_path, extracted_items[0])
            ):
                old_dir = os.path.join(temp_path, extracted_items[0])
                new_dir = os.path.join(temp_path, "lnd")
                if old_dir != new_dir:
                    if os.path.exists(new_dir):
                        import shutil

                        shutil.rmtree(new_dir)
                    os.rename(old_dir, new_dir)


def download_ckb(ckb_version, last_one=False):
    """
    download ckb from gitHub by ckb version
    :param ckb_version: gitHub release ckb version
    :return: None
    """
    system = platform.system()
    architecture = platform.machine() if system in ["Linux", "Darwin"] else ""
    print(
        f"system:{system},architecture:{architecture}".format(
            system=system, architecture=architecture
        )
    )
    url = SYSTEMS[system][architecture]["url"].format(version=ckb_version)
    ext = SYSTEMS[system][architecture]["ext"]

    filename = f"lnd_{ckb_version}_binary{ext}"
    download_path = os.path.join(DOWNLOAD_DIR, ckb_version).split("-")[0]
    os.makedirs(download_path, exist_ok=True)

    download_file(url, filename)
    if last_one:
        current_download_path = os.path.join(DOWNLOAD_DIR, "current")
        extract_file(filename, current_download_path)
    else:
        extract_file(filename, download_path)


for i in range(len(versions)):
    current = i == len(versions) - 1
    download_ckb(versions[i], current)

# for version in versions:
#
#     download_ckb(version)
