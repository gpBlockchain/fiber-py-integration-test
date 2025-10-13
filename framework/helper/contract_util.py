from framework.test_node import CkbNode
from framework.helper.miner import miner_with_version


# -> dict[str, CkbContract]
def deploy_contracts(account_private, node: CkbNode):
    # if tip number < 10
    # miner to 10 block
    if node.getClient().get_tip_block_number() < 10:
        for i in range(20):
            miner_with_version(node, "0x0")
    # deploy contract
    spawn = SpawnContract()
    spawn.deploy(account_private, node)
    return {"SpawnContract": spawn}
