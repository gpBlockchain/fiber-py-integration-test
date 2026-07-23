"""
经真实 Tor 的 Fiber 集成测试（PR #1228）。

用例固定 SOCKS **127.0.0.1:9050**、Control **127.0.0.1:9051**（与常见 tor 默认一致）。
1) 若上述端口已有可用服务则复用；
2) 否则 PATH 中有 ``tor`` 时用 ``TorDaemon`` 以命令行绑定 9050/9051 自启（读取系统默认 torrc，
   端口与控制密码由命令行覆盖），并配置 ``fiber_tor_password``。

https://github.com/nervosnetwork/fiber/pull/1228
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from typing import Optional

import pytest

from framework.basic_fiber import FiberTest
from framework.test_fiber import Fiber, FiberConfigPath
from framework.tor_daemon import TorDaemon
from framework.util import get_project_root

TOR_HOST = "127.0.0.1"
TOR_SOCKS_PORT = 9050
TOR_CONTROL_HOST = "127.0.0.1"
TOR_CONTROL_PORT = 9051
ONION_EXTERNAL_PORT = 18115

# 仅用于测试自启 TorDaemon；与命令行 --HashedControlPassword 一致，需配 fiber_tor_password
MANAGED_TOR_PASSWORD = "fiber-pr1228-tor-ctl"


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class TorRuntime:
    """当前用例使用的 Tor 端点；若为自管实例，stop() 会关闭子进程。"""

    host: str
    socks_port: int
    control_host: str
    control_port: int
    tor_password: Optional[str]
    _daemon: Optional[TorDaemon] = None

    def onion_server(self) -> str:
        return f"{self.host}:{self.socks_port}"

    def tor_controller(self) -> str:
        return f"{self.control_host}:{self.control_port}"

    def fiber_tor_auth_config(self) -> dict:
        if self.tor_password is None:
            return {}
        return {"fiber_tor_password": self.tor_password}

    def stop(self) -> None:
        if self._daemon is not None:
            self._daemon.stop()
            self._daemon = None


def _skip_no_tor_binary() -> None:
    import shutil

    if shutil.which("tor") is None:
        pytest.skip("本机无可用 Tor SOCKS，且 PATH 中未找到 tor（如 brew install tor）")


def ensure_tor_socks_only(
    data_dir: str, *, wait_bootstrap: bool = True, bootstrap_timeout: float = 420.0
) -> TorRuntime:
    """仅需 SOCKS：9050 已监听则复用，否则在 9050/9051 自启 TorDaemon。"""
    if _tcp_reachable(TOR_HOST, TOR_SOCKS_PORT):
        return TorRuntime(
            TOR_HOST,
            TOR_SOCKS_PORT,
            TOR_CONTROL_HOST,
            TOR_CONTROL_PORT,
            None,
            None,
        )
    _skip_no_tor_binary()
    d = TorDaemon(
        data_dir=data_dir,
        control_password=MANAGED_TOR_PASSWORD,
        socks_port=TOR_SOCKS_PORT,
        control_port=TOR_CONTROL_PORT,
    )
    d.start()
    try:
        d.wait_ready(
            timeout=120,
            wait_bootstrap=wait_bootstrap,
            bootstrap_timeout=bootstrap_timeout,
        )
    except Exception:
        d.stop()
        raise
    return TorRuntime(
        TOR_HOST,
        TOR_SOCKS_PORT,
        TOR_CONTROL_HOST,
        TOR_CONTROL_PORT,
        MANAGED_TOR_PASSWORD,
        d,
    )


def ensure_tor_with_control(
    data_dir: str, *, wait_bootstrap: bool = True, bootstrap_timeout: float = 420.0
) -> TorRuntime:
    """需要 Control：9050 与 9051 均可达则复用本机 Tor，否则在 9050/9051 自启。"""
    if _tcp_reachable(TOR_HOST, TOR_SOCKS_PORT) and _tcp_reachable(
        TOR_CONTROL_HOST, TOR_CONTROL_PORT
    ):
        return TorRuntime(
            TOR_HOST,
            TOR_SOCKS_PORT,
            TOR_CONTROL_HOST,
            TOR_CONTROL_PORT,
            None,
            None,
        )
    _skip_no_tor_binary()
    d = TorDaemon(
        data_dir=data_dir,
        control_password=MANAGED_TOR_PASSWORD,
        socks_port=TOR_SOCKS_PORT,
        control_port=TOR_CONTROL_PORT,
    )
    d.start()
    try:
        d.wait_ready(
            timeout=120,
            wait_bootstrap=wait_bootstrap,
            bootstrap_timeout=bootstrap_timeout,
        )
    except Exception:
        d.stop()
        raise
    return TorRuntime(
        TOR_HOST,
        TOR_SOCKS_PORT,
        TOR_CONTROL_HOST,
        TOR_CONTROL_PORT,
        MANAGED_TOR_PASSWORD,
        d,
    )


def _tor_data_dir(test_case: object, name: str) -> str:
    m = getattr(test_case, "_testMethodName", "tor")
    return os.path.join(get_project_root(), "tmp", "pr1228_tor", m, name)


class TestPR1228TorOnion(FiberTest):
    """本机 Tor 注册 v3 隐藏服务，对端仅配 onion_server 经 Tor 拨入 .onion。"""

    def _base_fiber_update_config(self):
        deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()
        return {
            "ckb_rpc_url": self.node.rpcUrl,
            "ckb_udt_whitelist": True,
            "xudt_script_code_hash": self.Contract.get_ckb_contract_codehash(
                deploy_hash, deploy_index, True, self.node.rpcUrl
            ),
            "xudt_cell_deps_tx_hash": deploy_hash,
            "xudt_cell_deps_index": deploy_index,
        }

    def _wait_peer(self, client, remote_pubkey: str, timeout: float = 180.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            peers = client.list_peers().get("peers") or []
            for p in peers:
                if p.get("pubkey") == remote_pubkey:
                    return
            time.sleep(1.0)
        raise AssertionError(
            f"peer {remote_pubkey} not listed before timeout ({timeout}s)"
        )

    def _connect_peer_until_visible(
        self,
        dialer,
        listener,
        address: str,
        timeout: float = 300.0,
    ):
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                dialer.get_client().connect_peer({"address": address})
            except Exception as e:
                last_error = e
            dialer_peers = dialer.get_client().list_peers().get("peers") or []
            listener_peers = listener.get_client().list_peers().get("peers") or []
            if any(
                p.get("pubkey") == listener.get_pubkey() for p in dialer_peers
            ) and any(p.get("pubkey") == dialer.get_pubkey() for p in listener_peers):
                return
            time.sleep(5.0)
        raise AssertionError(
            "peer connection via onion was not visible on both nodes before "
            f"timeout ({timeout}s), last_error={last_error}"
        )

    def _wait_onion_multiaddr(self, client, timeout: float = 180.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            addrs = client.node_info().get("addresses") or []
            for a in addrs:
                if "/onion3/" in a:
                    return a
            time.sleep(2.0)
        raise AssertionError(
            f"node_info 中未出现 /onion3/ 地址（{timeout}s 内）；"
            "请确认 Tor Control 可用且已完成 bootstrap"
        )

    def _restart_managed_tor(self, rt: TorRuntime, log_dir: str) -> None:
        """仅当用例自启了 TorDaemon 时可重启；复用系统已有 tor 时 skip。"""
        if rt._daemon is None:
            pytest.skip(
                "需由本测试进程自启 Tor（9050 空闲且 PATH 有 tor）；"
                "当前复用已有 Tor 实例，无法在测试内安全重启"
            )
        rt.stop()
        d = TorDaemon(
            data_dir=log_dir,
            control_password=MANAGED_TOR_PASSWORD,
            socks_port=TOR_SOCKS_PORT,
            control_port=TOR_CONTROL_PORT,
        )
        d.start()
        d.wait_ready(
            timeout=120,
            wait_bootstrap=True,
            bootstrap_timeout=420.0,
        )
        rt._daemon = d

    def _wait_peers_recover(
        self,
        fiber_hs,
        fiber_client,
        onion_addr: str,
        timeout: float = 300.0,
    ) -> None:
        """tor 恢复后等待两侧 list_peers 再次出现对端；必要时重连 .onion。"""
        deadline = time.time() + timeout
        pk_hs = fiber_hs.get_pubkey()
        pk_c = fiber_client.get_pubkey()
        c_hs = fiber_hs.get_client()
        c_c = fiber_client.get_client()
        while time.time() < deadline:
            peers_hs = c_hs.list_peers().get("peers") or []
            peers_c = c_c.list_peers().get("peers") or []
            if any(p.get("pubkey") == pk_c for p in peers_hs) and any(
                p.get("pubkey") == pk_hs for p in peers_c
            ):
                return
            time.sleep(2.0)
        raise AssertionError(
            f"tor 重启后 {timeout}s 内 list_peers 未恢复双向 peer；"
            "请检查 onion 与控制端口是否就绪"
        )

    def test_peer_connect_via_onion_hidden_service(self):
        data_dir = _tor_data_dir(self, "onion_hs")
        rt = ensure_tor_with_control(data_dir, wait_bootstrap=True)
        try:
            cfg_extra = rt.fiber_tor_auth_config()
            onion_server = rt.onion_server()
            ctrl = rt.tor_controller()

            account_hs = self.generate_account(10000)
            cfg_hs = self._base_fiber_update_config()
            cfg_hs["fiber_listen_on_onion"] = True
            cfg_hs["fiber_announce_private_addr"] = False
            cfg_hs["fiber_onion_server"] = onion_server
            cfg_hs["fiber_tor_controller"] = ctrl
            cfg_hs["fiber_onion_external_port"] = ONION_EXTERNAL_PORT
            cfg_hs.update(cfg_extra)

            fiber_hs = self.start_new_fiber(account_hs, config=cfg_hs)
            time.sleep(3)
            onion_addr = self._wait_onion_multiaddr(
                fiber_hs.get_client(), timeout=240.0
            )

            account_client = self.generate_account(10000)
            cfg_c = self._base_fiber_update_config()
            cfg_c["fiber_onion_server"] = onion_server
            cfg_c.update(cfg_extra)
            fiber_onion_client = self.start_new_fiber(account_client, config=cfg_c)
            time.sleep(2)
            self._connect_peer_until_visible(
                fiber_onion_client,
                fiber_hs,
                onion_addr,
                timeout=300.0,
            )

            with open(
                os.path.join(fiber_onion_client.tmp_path, "node.log"),
                "r",
                errors="replace",
            ) as f:
                client_log = f.read()
            assert (
                "tcp_onion_config" in client_log
            ), "拨入 .onion 的节点应设置 tcp_onion_config（见 PR #1228 network.rs）"
            peers = fiber_onion_client.get_client().list_peers()
            assert peers["peers"][0]["address"] == onion_addr
            self.open_channel(
                fiber_onion_client, fiber_hs, 1000 * 100000000, 1000 * 100000000
            )
            nodes = fiber_onion_client.get_client().graph_nodes({})
            print("fiber_onion_client nodes:", nodes)
            assert nodes["nodes"][0]["addresses"][0] == onion_addr
        finally:
            rt.stop()

    @pytest.mark.skip("need wait 20 minutes")
    def test_channel_ready_restart_tor_reconnect_and_payment(self):
        """建链后重启 Tor，peer 从 list_peers 恢复，再 keysend 成功。"""
        data_dir = _tor_data_dir(self, "onion_restart_pay")
        rt = ensure_tor_with_control(data_dir, wait_bootstrap=True)
        try:
            cfg_extra = rt.fiber_tor_auth_config()
            onion_server = rt.onion_server()
            ctrl = rt.tor_controller()

            account_hs = self.generate_account(10000)
            cfg_hs = self._base_fiber_update_config()
            cfg_hs["fiber_listen_on_onion"] = True
            cfg_hs["fiber_announce_private_addr"] = False
            cfg_hs["fiber_onion_server"] = onion_server
            cfg_hs["fiber_tor_controller"] = ctrl
            cfg_hs["fiber_onion_external_port"] = ONION_EXTERNAL_PORT
            cfg_hs.update(cfg_extra)

            fiber_hs = self.start_new_fiber(account_hs, config=cfg_hs)
            time.sleep(3)
            onion_addr = self._wait_onion_multiaddr(
                fiber_hs.get_client(), timeout=240.0
            )

            account_client = self.generate_account(10000)
            cfg_c = self._base_fiber_update_config()
            cfg_c["fiber_onion_server"] = onion_server
            cfg_c.update(cfg_extra)
            fiber_onion_client = self.start_new_fiber(account_client, config=cfg_c)
            time.sleep(2)
            fiber_onion_client.get_client().connect_peer({"address": onion_addr})

            self._wait_peer(
                fiber_hs.get_client(), fiber_onion_client.get_pubkey(), timeout=180.0
            )
            self._wait_peer(
                fiber_onion_client.get_client(),
                fiber_hs.get_pubkey(),
                timeout=180.0,
            )

            self.open_channel(
                fiber_onion_client,
                fiber_hs,
                1000 * 100000000,
                1000 * 100000000,
            )

            self._restart_managed_tor(rt, data_dir)
            self._wait_peers_recover(
                fiber_hs, fiber_onion_client, onion_addr, timeout=300.0
            )

            self.send_payment(
                fiber_onion_client,
                fiber_hs,
                10 * 100000000,
                wait=True,
            )
        finally:
            rt.stop()


class TestPR1228TorTestnet:
    """经 Tor SOCKS5 连接测试网节点的测试用例（无需本地 devnet）。"""

    TESTNET_BOOTNODE_ADDRS = [
        "/ip4/54.179.226.154/tcp/8228/p2p/Qmes1EBD4yNo9Ywkfe6eRw9tG1nVNGLDmMud1xJMsoYFKy",
        "/ip4/54.179.226.154/tcp/18228/p2p/QmdyQWjPtbK4NWWsvy8s69NGJaQULwgeQDT5ZpNDrTNaeV",
    ]

    ACCOUNT_PRIVATE = (
        "0xaae4515b745efcd6f00c1b40aaeef3dd66c82d75f8f43d0f18e1a1eecb90ada4"
    )

    fiber: Fiber = None

    def _wait_any_peer(self, client, timeout: float = 120.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            peers = client.list_peers().get("peers") or []
            if peers:
                return peers[0]
            time.sleep(1.0)
        raise AssertionError(f"no peers connected before timeout ({timeout}s)")

    def _start_fiber_testnet(self, tmp_dir: str, extra_config: dict = None):
        config = {
            "fiber_listening_addr": "/ip4/127.0.0.1/tcp/18228",
            "rpc_listening_addr": "127.0.0.1:18227",
        }
        if extra_config:
            config.update(extra_config)

        fiber = Fiber(
            FiberConfigPath.CURRENT_TESTNET,
            self.ACCOUNT_PRIVATE,
            tmp_dir,
            config,
        )
        fiber.prepare(extra_config)
        fiber.start()
        return fiber

    def _cleanup_fiber(self):
        if self.fiber:
            try:
                self.fiber.stop()
            except Exception:
                pass
            try:
                self.fiber.clean()
            except Exception:
                pass
            self.fiber = None

    def test_connect_testnet_bootnode_via_tor(self):
        rt = ensure_tor_socks_only(
            _tor_data_dir(self, "testnet_socks"), wait_bootstrap=True
        )
        try:
            proxy_url = f"socks5://{rt.host}:{rt.socks_port}"

            extra_config = {
                "fiber_proxy_url": proxy_url,
                "fiber_proxy_random_auth": True,
            }
            self.fiber = self._start_fiber_testnet("fiber/testnet_tor", extra_config)
            time.sleep(2)

            connected = False
            for bootnode_addr in self.TESTNET_BOOTNODE_ADDRS:
                try:
                    self.fiber.get_client().connect_peer({"address": bootnode_addr})
                    connected = True
                    print(f"已发起连接请求至测试网 bootnode: {bootnode_addr}")
                    break
                except Exception as e:
                    print(f"连接 {bootnode_addr} 失败: {e}")
                    continue

            if not connected:
                pytest.skip("无法连接任何测试网 bootnode，可能是网络问题或节点不可用")

            try:
                peer = self._wait_any_peer(self.fiber.get_client(), timeout=180.0)
                print(f"成功连接到测试网节点: pubkey={peer.get('pubkey')}")
            except AssertionError:
                pytest.skip("连接测试网 bootnode 超时，可能是 Tor 网络延迟或节点不可用")

            log_path = os.path.join(self.fiber.tmp_path, "node.log")
            with open(log_path, "r", errors="replace") as f:
                log_text = f.read()
            assert (
                "tcp_proxy_config" in log_text
            ), "fnn 应记录 SOCKS 代理配置；请确认使用包含 PR #1228 的 fnn 构建"

            time.sleep(5)
            nodes = self.fiber.get_client().graph_nodes({})
            print(f"从测试网同步到 {len(nodes.get('nodes', []))} 个节点信息")

        finally:
            self._cleanup_fiber()
            rt.stop()

    def test_connect_testnet_bootnode_via_onion(self):
        data_dir = _tor_data_dir(self, "testnet_onion")
        rt = ensure_tor_with_control(data_dir, wait_bootstrap=True)
        try:
            proxy_url = f"socks5://{rt.host}:{rt.socks_port}"
            onion_server = rt.onion_server()
            ctrl = rt.tor_controller()
            cfg_auth = rt.fiber_tor_auth_config()

            extra_config = {
                "fiber_proxy_url": proxy_url,
                "fiber_proxy_random_auth": True,
                "fiber_listen_on_onion": True,
                "fiber_announce_private_addr": False,
                "fiber_tor_controller": ctrl,
                "fiber_onion_server": onion_server,
            }
            extra_config.update(cfg_auth)
            self.fiber = self._start_fiber_testnet("fiber/testnet_onion", extra_config)
            time.sleep(2)

            connected = False
            for bootnode_addr in self.TESTNET_BOOTNODE_ADDRS:
                try:
                    self.fiber.get_client().connect_peer({"address": bootnode_addr})
                    connected = True
                    print(f"已发起连接请求至测试网 bootnode: {bootnode_addr}")
                    break
                except Exception as e:
                    print(f"连接 {bootnode_addr} 失败: {e}")
                    continue

            if not connected:
                pytest.skip("无法连接任何测试网 bootnode，可能是网络问题或节点不可用")

            try:
                peer = self._wait_any_peer(self.fiber.get_client(), timeout=180.0)
                print(f"成功连接到测试网节点: pubkey={peer.get('pubkey')}")
            except AssertionError:
                pytest.skip("连接测试网 bootnode 超时，可能是 Tor 网络延迟或节点不可用")

            log_path = os.path.join(self.fiber.tmp_path, "node.log")
            with open(log_path, "r", errors="replace") as f:
                log_text = f.read()
            assert (
                "tcp_onion_config" in log_text
            ), "fnn 应记录 onion 配置；请确认使用包含 PR #1228 的 fnn 构建"

        finally:
            self._cleanup_fiber()
            rt.stop()
