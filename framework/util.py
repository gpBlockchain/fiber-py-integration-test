from jinja2 import Environment, FileSystemLoader, select_autoescape
import subprocess
import time
import json
import toml
import re
import struct

import hashlib

H256_ZEROS = "0x0000000000000000000000000000000000000000000000000000000000000000"

U128_MIN_COMPATIBLE = 0  # Adjust according to your definition
U128_MAX_COMPATIBLE = 2**128 - 1
ACCOUNT_PRIVATE_KEY_INDEX = 0
import random
import time
import subprocess
import os
from datetime import datetime, timedelta
import logging

LOGGER = logging.getLogger(__name__)


def to_json(value):
    return json.dumps(value)


def to_remove_str(value):
    return value[1:-1]


# ckb config ,ckb miner config ,ckb spec config
def get_ckb_configs(p2p_port, rpc_port, spec='{ file = "dev.toml" }'):
    return (
        {
            # 'ckb_chain_spec': '{ bundled = "specs/mainnet.toml" }',
            "ckb_chain_spec": spec,
            "ckb_network_listen_addresses": [
                "/ip4/0.0.0.0/tcp/{p2p_port}".format(p2p_port=p2p_port)
            ],
            "ckb_rpc_listen_address": "127.0.0.1:{rpc_port}".format(rpc_port=rpc_port),
            "ckb_rpc_modules": [
                "Net",
                "Pool",
                "Miner",
                "Chain",
                "Stats",
                "Subscription",
                "Experiment",
                "Debug",
                "IntegrationTest",
            ],
            "ckb_block_assembler_code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
            "ckb_block_assembler_args": "0x8883a512ee2383c01574a328f60eeccbb4d78240",
            "ckb_block_assembler_hash_type": "type",
            "ckb_block_assembler_message": "0x",
        },
        {
            "ckb_miner_rpc_url": "127.0.0.1:{rpc_port}".format(rpc_port=rpc_port),
            "ckb_chain_spec": spec,
        },
        {},
    )


def create_config_file(config_values, template_path, output_file):
    file_loader = FileSystemLoader(get_project_root())
    # 创建一个环境
    env = Environment(loader=file_loader, autoescape=select_autoescape(["html", "xml"]))
    # 添加新的过滤器
    env.filters["to_json"] = to_json
    env.filters["to_remove_str"] = to_remove_str
    # 加载模板
    template = env.get_template(template_path)

    if not os.path.exists(os.path.dirname(output_file)):
        os.makedirs(os.path.dirname(output_file))

    # 使用数据渲染模板
    output = template.render(**config_values)

    # 将渲染的模板写入文件
    with open(output_file, "w") as f:
        f.write(output)


def run_command(cmd, check_exit_code=True, env=None):
    if cmd[-1] == "&":
        cmd1 = "{cmd} echo $! > pid.txt".format(cmd=cmd)
        LOGGER.debug("cmd:{cmd}".format(cmd=cmd1))

        process = subprocess.Popen(cmd1, shell=True, env=env)
        time.sleep(1)
        LOGGER.debug(f"process PID:{process.pid}")
        with open("pid.txt", "r") as f:
            pid = int(f.read().strip())
            LOGGER.debug(f"PID:{pid}")
            # pid is new shell
            # pid+1 = run cmd
            # result:       55456  13.6  0.2 409387712  34448   ??  R     4:22PM   0:00.05 ./ckb run --indexer
            #        55457   5.8  0.0 34411380   2784   ??  S     4:22PM   0:00.02 /bin/sh -c ps aux | grep ckb
            #        55459   0.0  0.0 33726716   1836   ??  R     4:22PM   0:00.01 grep ckb
            #        55455   0.0  0.0 438105996   1508   ??  S     4:22PM   0:00.01 \
            #        /bin/sh -c cd /Users/guopenglin/WebstormProjects/gp/ckb-py-integration-test/tmp/node/node && \
            #        ./ckb run --indexer > node.log 2>&1 & echo $! > pid.txt
            return str(pid + 1)

    LOGGER.debug("cmd:{cmd}".format(cmd=cmd))
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, env=env
    )
    stdout, stderr = process.communicate()
    exit_code = process.returncode

    if exit_code != 0:
        LOGGER.debug(f"Command failed with exit code: {exit_code}")
        if stderr:
            LOGGER.debug(f'Error:{stderr.decode("utf-8")}')
        if not check_exit_code:
            return str(exit_code)
        raise Exception(stderr.decode("utf-8"))
    if stderr.decode("utf-8") != "" and stdout.decode("utf-8") != "":
        LOGGER.debug("wain:{result}".format(result=stderr.decode("utf-8")))
        LOGGER.debug("result:{result}".format(result=stdout.decode("utf-8")))
        return stdout.decode("utf-8")
    LOGGER.debug("result:{result}".format(result=stdout.decode("utf-8")))
    return stdout.decode("utf-8")


def get_project_root():
    current_path = os.path.dirname(os.path.abspath(__file__))
    pattern = r"(.*fiber-py-integration-test)"
    matches = re.findall(pattern, current_path)
    if matches:
        root_dir = max(matches, key=len)
        return root_dir
    else:
        raise Exception("not found fiber-py-integration-test dir")


def read_toml_file(file_path):
    try:
        with open(file_path, "r") as file:
            toml_content = file.read()
            config = toml.loads(toml_content)
            return config
    except Exception as e:
        LOGGER.debug(f"Error reading TOML file: {e}")
        return None


def to_big_uint128_le_compatible(num):
    if num < U128_MIN_COMPATIBLE:
        raise ValueError(f"u128 {num} too small")

    if num > U128_MAX_COMPATIBLE:
        raise ValueError(f"u128 {num} too large")

    buf = bytearray(16)

    for i in range(4):
        buf[i * 4 : i * 4 + 4] = struct.pack("<I", num & 0xFFFFFFFF)
        num >>= 32
    return "0x" + buf.hex()


def to_int_from_big_uint128_le(hex_str):
    # Strip the '0x' prefix if present
    if hex_str.startswith("0x"):
        hex_str = hex_str[2:]

    # Convert the hex string into a byte array (16 bytes for uint128)
    buf = bytearray.fromhex(hex_str)

    # Reverse the byte array to convert from little-endian to big-endian
    buf.reverse()

    # Convert the byte array into an integer
    result = int.from_bytes(buf, byteorder="big")

    return result


def ckb_hasher():
    return hashlib.blake2b(digest_size=32, person=b"ckb-default-hash")


def ckb_hash(message):
    hasher = ckb_hasher()
    hasher.update(bytes.fromhex(message.replace("0x", "")))
    return "0x" + hasher.hexdigest()


def ckb_hash_script(arg):
    arg = arg.replace("0x", "")
    pack_lock = f"0x490000001000000030000000310000009bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce80114000000{arg}"
    return ckb_hash(pack_lock)


def generate_random_preimage():
    hash_str = "0x"
    for _ in range(64):
        hash_str += hex(random.randint(0, 15))[2:]
    return hash_str


def generate_account_privakey():
    global ACCOUNT_PRIVATE_KEY_INDEX
    ACCOUNT_PRIVATE_KEY_INDEX = ACCOUNT_PRIVATE_KEY_INDEX + 1
    return f"{ACCOUNT_PRIVATE_KEY_INDEX}".zfill(64)


def change_time(hour):
    # 修改系统时间,加速1h
    try:
        # 获取当前时间并加1小时
        current_time = datetime.now()
        new_time = current_time + timedelta(hours=hour)

        # 格式化时间为系统命令需要的格式
        time_str = new_time.strftime("%m%d%H%M%Y")

        # 检测是否在Docker容器中运行
        is_docker = os.path.exists("/.dockerenv")

        if is_docker:
            # 在Docker容器中使用date命令修改系统时间（不需要sudo）
            cmd = f"date {time_str}"
            print(f"Docker环境 - 执行命令: {cmd}")
        else:
            # 在宿主机上使用sudo date命令修改系统时间

            cmd = f"echo hyperchain | sudo -S date {time_str}"
            print(f"宿主机环境 - 执行命令: sudo date {time_str}")

        # 执行系统命令
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            print("系统时间修改成功")
        else:
            print(f"系统时间修改失败: {result.stderr}")

    except Exception as e:
        print(f"修改系统时间时发生错误: {e}")

    print("updated time:", time.time())
    print("updated datetime:", datetime.now())


import os


def restore_time():
    """恢复系统时间"""
    print("开始恢复系统时间...")
    print("current time:", time.time())
    print("current datetime:", datetime.now())

    try:
        # 检测是否在Docker容器中运行
        is_docker = os.path.exists("/.dockerenv")

        if is_docker:
            # 在Docker容器中，尝试从网络同步时间
            cmd = "ntpdate -s time.nist.gov"
            print(f"Docker环境 - 执行命令: {cmd}")
        else:
            # 在宿主机上，使用sntp同步网络时间
            cmd = f"echo hyperchain | sudo -S sntp -sS time.apple.com"
            print(f"宿主机环境 - 执行命令: sudo sntp -sS time.apple.com")

        # 执行系统命令
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            print("系统时间恢复成功")
        else:
            print(f"系统时间恢复失败: {result.stderr}")
            # 如果网络同步失败，尝试手动减去1小时
            print("尝试手动恢复时间（减去1小时）...")
            current_time = datetime.now()
            restore_time_dt = current_time - timedelta(hours=1)
            time_str = restore_time_dt.strftime("%m%d%H%M%Y")

            if is_docker:
                cmd = f"date {time_str}"
            else:
                cmd = f"echo '{password}' | sudo -S date {time_str}"

            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                print("手动时间恢复成功")
            else:
                print(f"手动时间恢复失败: {result.stderr}")

    except Exception as e:
        print(f"恢复系统时间时发生错误: {e}")

    print("restored time:", time.time())
    print("restored datetime:", datetime.now())


if __name__ == "__main__":
    change_time(1)
