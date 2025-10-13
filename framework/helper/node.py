from framework.test_node import CkbNode
from framework.test_cluster import Cluster
import time
from functools import wraps


def tx_message(client, tx_hash):
    tx = client.get_transaction(tx_hash)
    inputs = []
    for i in range(len(tx["transaction"]["inputs"])):
        pre_cell = client.get_transaction(
            tx["transaction"]["inputs"][i]["previous_output"]["tx_hash"]
        )["transaction"]["outputs"][
            int(tx["transaction"]["inputs"][i]["previous_output"]["index"], 16)
        ]
        intput_cells.append(
            {"arg": pre_cell["lock"]["args"], "capacity": int(pre_cell["capacity"], 16)}
        )
    outputs = []
    return {"inputs": [], "outputs": []}


def wait_until_timeout(wait_times):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(wait_times):
                if func(*args, **kwargs):
                    return
                time.sleep(1)
            raise Exception("Timeout reached")

        return wrapper

    return decorator


@wait_until_timeout(wait_times=60)
def wait_get_transaction(node, tx_hash, status):
    return node.getClient().get_transaction(tx_hash)["tx_status"]["status"] == status


@wait_until_timeout(wait_times=60)
def wait_fetch_transaction(node, tx_hash, status):
    return node.getClient().fetch_transaction(tx_hash)["status"] == status


@wait_until_timeout(wait_times=60)
def wait_tx_pool(node, pool_key, gt_size):
    return int(node.getClient().tx_pool_info()[pool_key], 16) >= gt_size


def wait_node_height(node: CkbNode, num, wait_times):
    for i in range(wait_times):
        if node.getClient().get_tip_block_number() >= num:
            return
        time.sleep(1)
    raise Exception(
        f"time out ,node tip number:{node.getClient().get_tip_block_number()}"
    )


def wait_cluster_height(cluster: Cluster, num, wait_times):
    for ckb_node in cluster.ckb_nodes:
        wait_node_height(ckb_node, num, wait_times)


def wait_light_sync_height(ckb_light_node, height, wait_times):
    for i in range(wait_times):
        min_height = 99999999
        scripts = ckb_light_node.getClient().get_scripts()
        if len(scripts) == 0:
            raise Exception("script is empty")
        for script in scripts:
            min_height = min(min_height, int(script["block_number"], 16))
        if min_height >= height:
            return
        print(f"current min height:{min_height},expected:{height}")
        time.sleep(1)
    raise Exception(f"time out " f",node tip number:{min_height}<{height}")


def wait_cluster_sync_with_miner(cluster: Cluster, wait_times, sync_number=None):
    """
    miner can make
    :param cluster:
    :param wait_times:
    :param sync_number:
    :return:
    """
    if sync_number is None:
        sync_number = cluster.ckb_nodes[0].getClient().get_tip_block_number()
    cluster.ckb_nodes[0].start_miner()
    wait_cluster_height(cluster, sync_number, wait_times)
    cluster.ckb_nodes[0].stop_miner()
