import shutil
import time

import pytest

from framework.basic_fiber import FiberTest


class TestBackup(FiberTest):

    def test_backup(self):
        """
        1. 关闭节点
        2. 替换节点
        3. 重启，send payment
        Returns:
        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        before_graph_nodes = self.fiber2.get_client().graph_nodes()
        self.fiber1.stop()
        self.fiber1.prepare({"fiber_listening_addr": "/ip4/127.0.0.1/tcp/8238"})
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(5)
        after_graph_nodes = self.fiber2.get_client().graph_nodes()
        self.send_payment(self.fiber1, self.fiber2, 1)
        print(before_graph_nodes)
        print(after_graph_nodes)
        assert (
            after_graph_nodes["nodes"][1]["addresses"]
            == self.fiber1.get_client().node_info()["addresses"]
        )

    def test_backup2(self):
        """
        1. 不关闭节点
        2. 直接备份数据
        3. 重启，send payment
        Returns:
        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        shutil.copytree(
            f"{self.fiber1.tmp_path}/fiber", f"{self.fiber1.tmp_path}/fiber.bak"
        )
        self.fiber1.stop()
        shutil.rmtree(f"{self.fiber1.tmp_path}/fiber")
        shutil.copytree(
            f"{self.fiber1.tmp_path}/fiber.bak", f"{self.fiber1.tmp_path}/fiber"
        )
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(5)
        self.send_payment(self.fiber1, self.fiber2, 1)

    @pytest.mark.skip("restart ,list_peer will empty ,not stable")
    def test_backup3(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.fiber1.stop()
        self.fiber1.prepare({"fiber_listening_addr": "/ip4/127.0.0.1/tcp/8238"})
        self.fiber1.start("newPassword2")
        time.sleep(5)
        self.send_payment(self.fiber1, self.fiber2, 1)
        self.fiber1.stop()
        self.fiber1.start("newPassword2")
        time.sleep(5)
        peers = self.fiber1.get_client().list_peers()
        self.fiber1.stop()
        self.fiber1.start("newPassword2")
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(8)
        peers = self.fiber1.get_client().list_peers()
        assert len(peers["peers"]) > 0
        self.send_payment(self.fiber1, self.fiber2, 1)
