import time

import pytest

from framework.basic_fiber import FiberTest
from framework.util import generate_account_privakey


class TestGraphNodes(FiberTest):
    """

    1. add nodes

    2. remove nodes
        todo
    3.  更新

    3. 测试迭代
    """

    # @pytest.mark.skip("todo 待确定")
    def test_add_nodes(self):
        """
        add nodes
        """
        # add nodes
        new_fibers = []
        current_fiber = self.start_new_fiber(generate_account_privakey())
        for i in range(10):
            new_fiber = self.start_new_fiber(generate_account_privakey())
            current_fiber.connect_peer(new_fiber)
            new_fibers.append(new_fiber)
            current_fiber = new_fiber
        time.sleep(1)
        current_fiber.connect_peer(self.fiber1)

        current_fiber1 = self.start_new_fiber(generate_account_privakey())
        current_fiber1.connect_peer(self.fiber2)
        current_fiber2 = self.start_new_fiber(generate_account_privakey())
        current_fiber2.connect_peer(current_fiber)
        # wait fiber3 nodes == 15
        self.wait_graph_nodes(self.fibers[3], 15)
        assert len(current_fiber.get_client().graph_nodes()["nodes"]) == 15
        assert len(current_fiber1.get_client().graph_nodes()["nodes"]) == 15
        assert len(current_fiber2.get_client().graph_nodes()["nodes"]) == 15
        assert len(self.fiber1.get_client().graph_nodes()["nodes"]) == 15
        assert len(self.fiber2.get_client().graph_nodes()["nodes"]) == 15
        # 测试迭代
        idx = 0

        for fiber in self.fibers:
            graph_nodes = fiber.get_client().graph_nodes()
            print(
                f"idx:{idx}, current:{len(graph_nodes['nodes'])}",
                len(graph_nodes["nodes"]),
            )
            idx += 1
            graph_nodes = get_graph_nodes(fiber, 3)
            total_graph_nodes = fiber.get_client().graph_nodes()
            assert len(graph_nodes) == len(total_graph_nodes["nodes"])
            assert graph_nodes == total_graph_nodes["nodes"]
            graph_nodes = get_graph_nodes(fiber, 1)
            assert len(graph_nodes) == len(total_graph_nodes["nodes"])
            assert graph_nodes == total_graph_nodes["nodes"]
            graph_nodes = get_graph_nodes(fiber, 10)
            assert len(graph_nodes) == len(total_graph_nodes["nodes"])
            assert graph_nodes == total_graph_nodes["nodes"]
            graph_nodes = get_graph_nodes(fiber, 25)
            assert len(graph_nodes) == len(total_graph_nodes["nodes"])
            assert graph_nodes == total_graph_nodes["nodes"]
            graph_nodes = get_graph_nodes(fiber, 1000)
            assert len(graph_nodes) == len(total_graph_nodes["nodes"])
            assert graph_nodes == total_graph_nodes["nodes"]

    def test_node_info(self):
        graph_nodes = self.fiber1.get_client().graph_nodes()
        print(graph_nodes)
        node_info = self.fiber1.get_client().node_info()
        if graph_nodes["nodes"][0]["node_id"] != node_info["node_id"]:
            graph_nodes["nodes"].reverse()
        for i in range(len(graph_nodes["nodes"])):
            node = graph_nodes["nodes"][i]
            node_info = self.fibers[i].get_client().node_info()
            # alias
            # assert node['alias'] == node_info['node_name']
            # assert node["alias"] == ""
            # addresses
            assert node["addresses"] == node_info["addresses"]
            # node_id
            assert node["node_id"] == node_info["node_id"]
            # timestamp
            assert int(node["timestamp"], 16) <= int(time.time() * 1000)
            # chain_hash
            assert node["chain_hash"] == node_info["chain_hash"]
            # auto_accept_min_ckb_funding_amount
            assert (
                node["auto_accept_min_ckb_funding_amount"]
                == node_info["open_channel_auto_accept_min_ckb_funding_amount"]
            )
            # udt_cfg_infos
            assert node["udt_cfg_infos"] == node_info["udt_cfg_infos"]

            # features
            assert (
                node["features"] == node_info["features"]
            ), f"node:{node}, node_info:{node_info}"

    # @pytest.mark.skip("其他节点的graph_nodes 不一定会更新")
    def test_change_node_info(self):
        """
        1. 修改配置 ，重启节点
        Returns:
        """
        before_node_info = self.fiber1.get_client().node_info()
        self.fiber1.stop()
        self.fiber1.prepare({"fiber_auto_accept_amount": "100000001"})
        self.fiber1.start()
        time.sleep(3)
        node_info = self.fiber1.get_client().node_info()
        print("before_node_info:", before_node_info)
        print("after_node_info:", node_info)
        graph_nodes = self.fiber1.get_client().graph_nodes()
        if graph_nodes["nodes"][0]["node_id"] != node_info["node_id"]:
            graph_nodes["nodes"].reverse()
        for i in range(len(graph_nodes["nodes"])):
            node = graph_nodes["nodes"][i]
            node_info = self.fibers[i].get_client().node_info()
            # alias
            # assert node['alias'] == node_info['node_name']
            # addresses
            assert node["addresses"] == node_info["addresses"]
            # node_id
            assert node["node_id"] == node_info["node_id"]
            # timestamp
            assert int(node["timestamp"], 16) <= int(time.time() * 1000)
            # chain_hash
            assert node["chain_hash"] == node_info["chain_hash"]
            # auto_accept_min_ckb_funding_amount
            assert (
                node["auto_accept_min_ckb_funding_amount"]
                == node_info["open_channel_auto_accept_min_ckb_funding_amount"]
            )
            # udt_cfg_infos
            assert node["udt_cfg_infos"] == node_info["udt_cfg_infos"]
        # check other nodes
        graph_nodes = self.fiber2.get_client().graph_nodes()
        node_info = self.fiber1.get_client().node_info()
        if graph_nodes["nodes"][0]["node_id"] != node_info["node_id"]:
            print(
                "graph_nodes['nodes'][0]['node_id']:",
                graph_nodes["nodes"][0]["node_id"],
            )
            print("public key:", node_info["node_id"])
            graph_nodes["nodes"].reverse()
        print("graph_nodes['nodes']:", graph_nodes["nodes"])
        for i in range(len(graph_nodes["nodes"])):
            node = graph_nodes["nodes"][i]
            node_info = self.fibers[i].get_client().node_info()
            # addresses
            assert node["addresses"] == node_info["addresses"]
            # node_id
            assert node["node_id"] == node_info["node_id"]
            # timestamp
            assert int(node["timestamp"], 16) <= int(time.time() * 1000)
            # chain_hash
            assert node["chain_hash"] == node_info["chain_hash"]
            # auto_accept_min_ckb_funding_amount
            assert (
                node["auto_accept_min_ckb_funding_amount"]
                == node_info["open_channel_auto_accept_min_ckb_funding_amount"]
            )
            # udt_cfg_infos
            assert node["udt_cfg_infos"] == node_info["udt_cfg_infos"]

    def wait_graph_nodes(self, fiber, number, time_out=30):
        start_time = time.time()
        while True:
            graph_nodes = fiber.get_client().graph_nodes()
            if len(graph_nodes["nodes"]) == number:
                return
            time.sleep(1)
            if time.time() - start_time > time_out:
                raise Exception("wait graph nodes timeout")


def get_graph_nodes(fiber, page_size):
    after = None
    graph_nodes_ret = []
    while True:
        graph_nodes = fiber.get_client().graph_nodes(
            {"limit": hex(page_size), "after": after}
        )
        if len(graph_nodes["nodes"]) == 0:
            return graph_nodes_ret
        assert len(graph_nodes["nodes"]) <= page_size
        graph_nodes_ret.extend(graph_nodes["nodes"])
        after = graph_nodes["last_cursor"]
